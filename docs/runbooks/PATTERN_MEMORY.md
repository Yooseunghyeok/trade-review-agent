# Pattern Memory Contract

Hermes may propose pattern candidates, but it must not confirm them.

Pattern states:

- `CANDIDATE`: generated from review evidence and waiting for user confirmation.
- `CONFIRMED`: explicitly confirmed by the user with an answer recorded.

Every record must include `episode_id`, `date`, `evidence_id`, and `user_answer`. Future daily reviews may cite only `CONFIRMED` records as factual memory. The skill must not change risk policy or trading rules from memory records.
