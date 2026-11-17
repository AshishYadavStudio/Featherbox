"""Reading JSON from the text model, including the retry when llama-server rejects a fenced reply."""
import json
import unittest

from tests import _env  # noqa: F401

import httpx

from app.engines.llm import LLMEngine

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

    def test_not_json(self):
        for bad in ("Sorry, I can't help with that.", "{broken", "[1, 2]"):
            with self.assertRaisesRegex(RuntimeError, "valid JSON"):
                LLMEngine._parse_json(bad, SCHEMA)


class ChatJsonFallback(unittest.TestCase):
    def engine(self, replies):
        e, calls = LLMEngine(), []

        def chat(messages, model_id=None, **kw):
            calls.append({"schema": kw.get("schema"), "last": messages[-1]["content"]})
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
        self.assertIn("JSON schema", calls[1]["last"])
        self.assertEqual(msgs[-1]["content"], "hi", "the caller's messages must not be changed")

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
