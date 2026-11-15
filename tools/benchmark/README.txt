Featherbox benchmark
====================

Measures how fast Featherbox runs on this PC with the models you have installed:
text (chat and Studio scripts), image, voice and speech-to-text, plus optionally a
complete Studio Short and an AI video clip. While each test runs it records CPU,
RAM and GPU use (busy % and GPU memory, including shared memory on laptops
without a dedicated graphics card).

How to run
----------
1. Unzip this folder anywhere (for example on the Desktop).
2. Plug the laptop in and close other heavy programs.
3. Double-click Run-Benchmark.bat and answer the three questions.
   Featherbox is started automatically if it isn't running.

Nothing extra needs to be installed: it uses the Python that comes with Featherbox.
Test pictures and recordings are deleted from your Featherbox galleries afterwards.

Result
------
A folder and a .zip named "Featherbox-benchmark-<PC name>-<date>" appear on the
Desktop. Send the .zip back. It contains:
  report.md    readable summary, with this PC next to the reference laptop
               (Ryzen 7 5800H + RTX 3050 4 GB)
  report.json  all numbers
  outputs\     the picture, voice recordings, transcripts and scripts it made
  logs\        the recent engine logs (to diagnose any failure)

No personal files are included, only Featherbox's own test output and logs.
