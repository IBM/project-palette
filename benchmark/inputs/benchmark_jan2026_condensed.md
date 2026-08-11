# Enterprise Tool-Calling Benchmark

Audience: IBM benchmark and agent-evaluation team
Slides: 14

Preferences:
- Background: light
- Tone: technical, precise, factual

## Cover

Enterprise Tool-Calling Benchmark — Release: March 2026.

## Agenda

1. Benchmark description.
2. Component datasets — LiveAPIBench, M3, BPO, Oak Health Insurance / Elevance.
3. Tools supporting the dataset — the environment.
4. Release plan.
5. Data description — data statistics and baselines.
6. Next steps.

## Enterprise Tool-Calling: What Our Benchmark Covers

Nine characteristics of enterprise tool-calling, and what the benchmark provides for each.
- Multiple domains : 70+ domains.
- Joint reasoning over structured and unstructured knowledge sources, hosted via APIs : API and RAG in all combinations.
- Multi-hop reasoning : 3 to 7 hops.
- Large tool sets : a universe of 8,000+ live APIs.
- Complex payloads, both input and output : multiple input and output fields.
- Large tool-response objects : tool responses can exceed 200K data points.
- Noisy, imprecise schemas in API documentation and API data : real-world databases sourced from public sources.
- API styles vary, for example Dashboard APIs versus Business Intelligence APIs : supported.
- Tool-use policies : supported.

## Tasks — Single Turn

The single-turn tasks, grouped by API style and by reasoning type.
- API styles:
  - Single-turn QA with Business Intelligence APIs — Schema-Parametrized APIs (SLOT) and Schema-Bound APIs (SEL).
  - Single-turn QA with Dashboard APIs — REST APIs.
- Reasoning:
  - Single-turn multi-hop reasoning — sourced from the BPO dataset and from M3 single-turn (all types: API-API, API-only).
  - Single-turn QA with policy adherence — sourced from Oak Health Insurance and from M3 tool-use policy.
- Note: internal dataset names are mentioned only to track the source of data for each task.

## Tasks — Multi-turn

The multi-turn task family.
- Multi-turn : the multi-turn version of the reasoning tasks.
- Extended to include hops involving retrievers — RAG-RAG, API-RAG, and RAG-API.

## Evaluation

How submissions are scored.
- Metrics:
  - Success rate = correct tool calls + answer — measured both at the turn-level task completion rate and at the dialog-level task completion rate.
  - Tool-calling F1.
  - Reasoning steps.
- Leaderboard ranking : a weighted score over the constituent task groups — API styles, reasoning, and multi-turn.
- Score = (0.3 x api_styles) + (0.4 x reasoning_styles) + (0.3 x multi-turn).

## Leaderboard Entry — Process Flow

How a submission reaches the leaderboard — a five-step flow, left to right.
1. Download data — the test set, with no ground-truth answers visible.
2. Host the local environment.
3. Run the local agent.
4. Submit the output JSON.
5. Run the scorer and update the leaderboard.

Steps 1 to 4 are the user's steps; step 5 is run by the benchmark team, automated via Kaggle (TBD).
Primary advantage: no overhead of running custom agents from users.
A training and dev split, with an RL environment, will also be released for agent developers.

## Input Schema — Test Data

The test data is a JSON array of dialogue samples. Each sample carries the dialogue turns and the tool universe; ground truth is not included.

```json
{
  "sample_id": "91_sc_ONLY_API_OUT_DOMAIN",
  "domain": "olympics",
  "num_turns": 2,
  "dialogue": {
    "turns": [
      { "query": "Which Summer Olympics did the competitor who has won the most medals win four gold and two silver medals?" },
      { "query": "What is the ratio of male to female athletes in those Games?" }
    ]
  },
  "tools": [
    { "name": "get_athlete_most_medals", "arguments": { "medal_type": "string" } },
    { "name": "get_games_by_athlete_medals", "arguments": { "athlete": "string", "gold": "int", "silver": "int" } }
  ],
  "additional_instructions": "Only use retrievers for the Retail, Sales & Commerce domain."
}
```

## Expected Output Schema — Test Data

The expected output mirrors each sample and adds the ground-truth answer plus the gold tool-call sequence.

```json
{
  "sample_id": "91_sc_ONLY_API_OUT_DOMAIN",
  "domain": "olympics",
  "ground_truth": [
    {
      "query": "Which Summer Olympics did the competitor who has won the most medals win four gold and two silver medals?",
      "answer": ["2012 Summer"],
      "gold_sequence": [
        {
          "tool_call": [
            {
              "name": "get_athlete_most_medals",
              "arguments": { "medal_type": "all" }
            }
          ],
          "tool_response": [["Michael Fred Phelps, II"]]
        }
      ]
    }
  ]
}
```

## M3 Dataset

The M3 dataset is built around dialogues, with three defining properties.
- Multi-Turn : every dialogue has 1 to 7 turns.
- Multi-Hop : every turn can carry a multi-hop question; questions have 1 to 3 hops.
- Multi-Source : every question can require multiple data sources for reasoning.

Example of a 3-turn dialogue:
- Turn 1 — "Provide a list of directors from the 1990s." Answer: "Barry Cook, Ron Clements, Wolfgang Reitherman, ..."
- Turn 2 — "Who voiced the hero in the Ron Clements movie which has the song 'Under the Sea'?" Answer: "Jodi Benson."
- Turn 3 — "And who's the villain in the Disney movie whose sequel, prequel, and TV spin-off was also voiced by her?" Answer: "Ursula."

## Dashboard APIs — Example

An example of a multi-source question answered with a single API call — the API is from the REST-BIRD collection. The flow:
- The question : "When was the football team with a build-up play speed of 31, build-up play dribbling of 53, and build-up play passing of 32 established?"
- One API call resolves the team : get_distinct_teams_by_build_up_play_attributes_v1_bird_european_football_2(build_up_play_speed=31, build_up_play_dribbling=53, build_up_play_passing=32) — it returns the team GLA.
- The question is now : "When was GLA established?"
- A RAG step answers it : "GLA was established in 1872."

## Business Intelligence — Schema-Bound Tools

An example of the same multi-source question, this time answered with sequential tool calls — the tools are from the SEL-BIRD API collection. The flow:
- The question : "When was the football team with a build-up play speed of 31, build-up play dribbling of 53, and build-up play passing of 32 established?"
- Four sequential, schema-bound calls narrow the data, each feeding the next:
  1. select_data_equal_to(data_source="$starting_table_var$", key_name="Team_Attributes_buildUpPlaySpeed", value=31.0, label="FILTERED_DF_0")
  2. select_data_equal_to(data_source="$FILTERED_DF_0$", key_name="Team_Attributes_buildUpPlayDribbling", value=53.0, label="FILTERED_DF_1")
  3. select_data_equal_to(data_source="$FILTERED_DF_1$", key_name="Team_Attributes_buildUpPlayPassing", value=32.0, label="FILTERED_DF_2")
  4. get_Team_team_short_names(data_source="$FILTERED_DF_2$", n=1, label="SELECT_COL_0")
- The result resolves the question to : "When was GLA established?"
- A RAG step answers it : "GLA was established in 1872."

## Multi-hop, Multi-turn Dialog with REST Endpoints

One dialogue mixing 1-hop, 2-hop, and 3-hop queries — each query needs reasoning across one or more steps.
- 1-hop (API) — "Provide a list of directors from the 1990s." One call: get_directors(year=1990). Answer: "Barry Cook, Ron Clements, Wolfgang Reitherman, ..."
- 3-hop (API-API-API) — "Who voiced the hero in the Ron Clements movie which has the song 'Under the Sea'?" Three chained calls: get_movie(director="Ron Clements", song="Under the Sea"), then get_character(movie="The Little Mermaid", role="hero"), then get_voice_actor(character="Ariel"). Answer: "Jodi Benson."
- 2-hop (RAG-API) — "And who's the villain in the Disney movie whose sequel, prequel, and TV spin-off was also voiced by her?" A RAG step retrieves the Jodi Benson biography, then get_villain_by_movie(movie_name="The Little Mermaid"). Answer: "Ursula."

## BFCL 4.0 vs M3

A side-by-side comparison of BFCL 4.0 and M3 across nine characteristics.
- Multi-turn multi-hop : BFCL 4.0 — no; M3 — yes.
- Multi-hop single turn : BFCL 4.0 — yes; M3 — yes.
- Max hops : BFCL 4.0 — 4; M3 — 4.
- Number of tools : BFCL 4.0 — 2; M3 — 9,111.
- Scenario injection : BFCL 4.0 — 6; M3 — 10.
- Number of dialogues : BFCL 4.0 — ~100; M3 — 7,000+.
- Number of domains : BFCL 4.0 — 8; M3 — 47.
- Memory tools : BFCL 4.0 — yes; M3 — no.
- Offline documents : BFCL 4.0 — no; M3 — yes.
