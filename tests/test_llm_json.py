"""Reading JSON from the text model, including the retry when llama-server rejects a fenced reply."""
import json
import unittest

from tests import _env  # noqa: F401

import httpx

from unittest import mock

from app import catalog, config
from app.engines import llm as llm_module
from app.engines.llm import BadReply, LLMEngine, json_example
from app.recipes.topic_to_short import script_schema, split_hashtags

SCHEMA = {"type": "object", "required": ["title", "scenes"],
          "properties": {"title": {"type": "string"}, "scenes": {"type": "array"}}}
GOOD = {"title": "Why is the Sky Blue?", "scenes": [{"narration": "a", "image_prompt": "b"}]}


def http_error(status: int, body: str = "") -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://127.0.0.1:8421/v1/chat/completions")
    return httpx.HTTPStatusError(str(status), request=req, response=httpx.Response(status, request=req, text=body))


class ParseJson(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(LLMEngine._parse_json(json.dumps(GOOD), SCHEMA), GOOD)

    def test_code_fence(self):  # what Gemma 3 1B actually returned on the Ryzen AI laptop
        self.assertEqual(LLMEngine._parse_json("```json\n" + json.dumps(GOOD, indent=2) + "\n```", SCHEMA), GOOD)

    def test_text_around(self):
        self.assertEqual(LLMEngine._parse_json("Here is the script:\n" + json.dumps(GOOD) + "\nEnjoy!", SCHEMA), GOOD)

    def test_missing_keys(self):
        with self.assertRaisesRegex(RuntimeError, "missing scenes"):
            LLMEngine._parse_json('{"title": "x"}', SCHEMA)

    def test_key_case_and_wrapper(self):  # slips small models make
        self.assertEqual(LLMEngine._parse_json('{"Title": "x", "Scenes": []}', SCHEMA), {"title": "x", "scenes": []})
        self.assertEqual(LLMEngine._parse_json(json.dumps({"script": GOOD}), SCHEMA), GOOD)
        self.assertEqual(LLMEngine._parse_json(json.dumps({"output": {"TITLE": "x", "scenes": []}}), SCHEMA),
                         {"title": "x", "scenes": []})

    def test_echoed_schema_is_named(self):
        with self.assertRaisesRegex(BadReply, "repeated the format description"):
            LLMEngine._parse_json(json.dumps(SCHEMA), SCHEMA)

    def test_repairs_seen_from_gemma(self):
        # real Gemma 3 1B slips (17 Nov): a stray "}" after the object, and a comma before "}"
        self.assertEqual(LLMEngine._parse_json("```json\n" + json.dumps(GOOD) + "\n }\n}\n```", SCHEMA), GOOD)
        self.assertEqual(LLMEngine._parse_json('{"title": "x", "scenes": [{"a": "b",\n  },\n ],\n}', SCHEMA),
                         {"title": "x", "scenes": [{"a": "b"}]})

    def test_answer_inside_copied_schema(self):
        # what Gemma 3 1B wrote for the old fallback prompt, which showed it the JSON Schema
        wrapped = {"type": "object", "properties": GOOD}
        self.assertEqual(LLMEngine._parse_json(json.dumps(wrapped), SCHEMA), GOOD)
        # ...also when it left out keys that have defaults
        schema = {"type": "object", "required": ["title", "scenes", "hashtags"]}
        self.assertEqual(LLMEngine._parse_json(json.dumps(wrapped), schema, {"hashtags": []}), GOOD | {"hashtags": []})

    def test_defaults_fill_optional_keys(self):
        schema = {"type": "object", "required": ["title", "hashtags"]}
        d = {"hashtags": []}
        self.assertEqual(LLMEngine._parse_json('{"title": "x"}', schema, d), {"title": "x", "hashtags": []})
        with self.assertRaisesRegex(BadReply, "missing title"):
            LLMEngine._parse_json('{"hashtags": ["a"]}', schema, d)

    def test_hashtags(self):
        self.assertEqual(split_hashtags(["#volcanoes #geology #earth science", "volcanoes", "", 5]),
                         ["volcanoes", "geology", "earthscience"])
        self.assertEqual(split_hashtags("a, b"), ["a", "b"])
        self.assertEqual(len(split_hashtags([f"t{i}" for i in range(19)])), 8)
        self.assertEqual(split_hashtags(None), [])

    def test_example_shape(self):
        ex = json_example(script_schema("english"))
        self.assertEqual(list(ex), ["title", "scenes", "description", "hashtags"],
                         "scenes early: with them last, Gemma 3 1B stopped before writing them")
        self.assertEqual(ex["scenes"], [{"narration": "...", "image_prompt": "..."}])
        self.assertEqual(ex["hashtags"], ["..."])

    def test_not_json(self):
        for bad in ("Sorry, I can't help with that.", "{broken", "[1, 2]"):
            with self.assertRaisesRegex(RuntimeError, "valid JSON"):
                LLMEngine._parse_json(bad, SCHEMA)


class ChatJsonFallback(unittest.TestCase):
    def engine(self, replies):
        e, calls = LLMEngine(), []

        def chat(messages, model_id=None, **kw):
            calls.append({"schema": kw.get("schema"), "last": messages[-1]["content"], "temp": kw.get("temperature")})
            r = replies[len(calls) - 1]
            if isinstance(r, Exception):
                raise r
            return r
        e.chat = chat
        return e, calls

    def test_schema_works_first_time(self):
        e, calls = self.engine([json.dumps(GOOD)])
        self.assertEqual(e.chat_json([{"role": "user", "content": "hi"}], SCHEMA), GOOD)
        self.assertEqual(len(calls), 1)

    def test_500_retries_without_schema(self):
        err = http_error(500, '{"error":{"message":"does not match the expected peg-native format"}}')
        e, calls = self.engine([err, "```json\n" + json.dumps(GOOD) + "\n```"])
        msgs = [{"role": "user", "content": "hi"}]
        self.assertEqual(e.chat_json(msgs, SCHEMA), GOOD)
        self.assertEqual([c["schema"] is not None for c in calls], [True, False])
        self.assertIn('"title": "..."', calls[1]["last"], "the retry shows an example of the shape")
        self.assertNotIn('"properties"', calls[1]["last"], "not the schema, which small models copy back")
        self.assertEqual(msgs[-1]["content"], "hi", "the caller's messages must not be changed")

    def test_benchmark_failure_now_recovers(self):
        # 17 Nov benchmark, Gemma 3 1B, English script: the enforced format failed with a 500, then the
        # plain answer lacked title and scenes. Now there is one more, cooler try.
        err = http_error(500, '{"error":{"message":"does not match the expected peg-native format"}}')
        e, calls = self.engine([err, json.dumps({"description": "x", "hashtags": []}), json.dumps(GOOD)])
        self.assertEqual(e.chat_json([{"role": "user", "content": "hi"}], SCHEMA, temperature=0.6), GOOD)
        self.assertEqual([c["temp"] for c in calls], [0.6, 0.6, 0.3])
        log = (config.LOGS / "text-replies.log").read_text("utf-8")
        self.assertIn("missing title, scenes", log)
        self.assertIn('"description": "x"', log, "the unusable reply is kept for later")

    def test_unusable_enforced_answer_is_retried(self):
        e, calls = self.engine(['{"title": "x"}', json.dumps(GOOD)])
        self.assertEqual(e.chat_json([{"role": "user", "content": "hi"}], SCHEMA), GOOD)
        self.assertEqual([c["schema"] is not None for c in calls], [True, False])

    def test_gives_up_after_three_tries_and_names_a_bigger_model(self):
        e, calls = self.engine([http_error(500, "x"), json.dumps(SCHEMA), '{"title": "x"}'])
        e.model_id = "llm-gemma3-1b"
        have = [catalog.get("llm-gemma3-1b"), catalog.get("llm-qwen3-4b"), catalog.get("llm-gemma3-12b")]
        with mock.patch.object(llm_module.catalog, "installed", return_value=have):
            with self.assertRaisesRegex(BadReply, r"missing scenes\. Gemma 3 1B is too small .* switch to "
                                                  r"Qwen3 4B Instruct \(already installed\)"):
                e.chat_json([{"role": "user", "content": "hi"}], SCHEMA)
        self.assertEqual(len(calls), 3)

    def test_gives_up_without_a_bigger_model(self):
        e, _ = self.engine(["nope", "nope", "nope"])
        with self.assertRaisesRegex(BadReply, "did not return valid JSON. The text model may be too small"):
            e.chat_json([{"role": "user", "content": "hi"}], SCHEMA)

    def test_second_failure_is_readable(self):
        e, _ = self.engine([http_error(500, "first"), http_error(500, "engine crashed")])
        with self.assertRaisesRegex(RuntimeError, "engine crashed"):
            e.chat_json([{"role": "user", "content": "hi"}], SCHEMA)

    def test_other_errors_are_not_retried(self):
        e, calls = self.engine([http_error(400, "bad request")])
        with self.assertRaises(httpx.HTTPStatusError):
            e.chat_json([{"role": "user", "content": "hi"}], SCHEMA)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
