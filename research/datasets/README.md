# Spider-derived pilot data

`spider-pilot.json` contains the first 20 distinct SQL strings for
`concert_singer`, in the order published in the official Spider development
examples. The original question numbers, pinned source URLs, database mirror,
and SHA-256 fingerprints are recorded in the JSON. No selection depends on
Claude's outputs. The subset includes the original annotations as published,
including their ambiguities; it is not a corrected benchmark.

Attribution: Tao Yu and the Spider contributors, *Spider: A Large-Scale
Human-Labeled Dataset for Complex and Cross-Domain Semantic Parsing and
Text-to-SQL Task*, EMNLP 2018. Official dataset:
https://yale-lily.github.io/spider . The dataset site distributes the data under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); this data subset
and its adaptation are distributed under that license. The repository's code
license does not replace the dataset's license.

The pilot adds `tenant_id` and a synthetic `_private_note` column, duplicates
the records into a second tenant, and changes selected text/measure values in
that second tenant. It uses one schema and 20 paired clean/injected questions.
It is a small integration experiment, not a Spider leaderboard score, a
held-out contamination-free assessment, or an estimate of LLM attack success
in production. Gold SQL is executed on a physically restricted reference DB.
