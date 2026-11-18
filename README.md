# ChipSeeker UltraSearch

Let your own Codex or Claude Code drive [ChipSeeker](https://chipseeker.online): interpret a research direction, expand missing terminology, run adaptive literature searches, screen and deduplicate results, and save linked Markdown and offline HTML reports.

## Connect With One Prompt

Send your agent:

> Install the ChipSeeker UltraSearch Skill from https://github.com/Yixuan-Miao/ChipSeeker-UltraSearch, connect to https://chipseeker.online using my API key [YOUR_KEY], and check my account balance.

Then, for example:

> UltraSearch pulsed cryogenic LNAs for superconducting-qubit readout. Save the results under D:\Research. Prioritize measured circuits, but retain clearly identified measurement-method papers when directly useful.

Email verification is available without a key: provide your registered email, then give the agent the six-digit verification code when asked. Never post real keys in public issues, screenshots or example prompts.

## Agent Installation Instructions

When the user requests installation or connection, perform the work with the available tools rather than handing manual download steps back to them:

1. Clone this repository, or obtain and extract its ZIP into a local tool directory. Do not clone the private service or download a paper database.
2. Find Python 3 and run `python install.py --client codex`, or `--client claude-code`. The installer uses only the Python standard library.
3. Read `SKILL.md` at the installed path and follow it in the current conversation. A new chat is not required to connect.
4. Configure the bundled `scripts/chipseeker_remote.py` using the user-supplied key, preferably through process-scoped `CHIPSEEKER_API_KEY`, and check `quota`. Do not echo credentials or store them in the repository or research output.
5. For email-only login, use `auth-send`, ask for the verification code, then use `auth-verify`. Retrieve the existing key; do not reset it.
6. If a topic and output path were also supplied, confirm the search objective and clarify material scope ambiguities before spending search credits. Do not ask again if the user's preference is already clear. Otherwise report connection status and balance briefly.

```text
git clone https://github.com/Yixuan-Miao/ChipSeeker-UltraSearch.git
cd ChipSeeker-UltraSearch
python install.py --client codex
```

Default locations are `~/.codex/skills/chipseeker-ultra-search` for Codex, respecting `CODEX_HOME`, and `~/.claude/skills/chipseeker-ultra-search` for Claude Code. Existing installations are backed up. Use `--skills-dir PATH` to choose another location.

To upgrade an existing installation, pull the latest `main` in this repository and rerun the installer for your client. The website does not automatically update an installed Skill. The account API key and search checkpoints are stored separately, so there is no need to reconnect or start a new search workspace.

## Accounts and Tokens

Each email has one active API key. Resetting the key invalidates the old key without changing the email-owned balance. Check live entitlement through the API rather than assuming it from a website membership name.

One UltraSearch Token pays for one successful Lite, Keyword or filtered-Lite API retrieval, including zero results. Abstract hydration is also a retrieval. Failed searches, authentication, quota checks, polling and local processing are free. An entire research session can use multiple Tokens; these are not language-model tokens. Account recharges and eligible one-time membership grants are administered by the service.

Your own AI performs query planning, expansion, screening and reporting. This public package contains a remote client and operating instructions, not the private service implementation, corpus or account secrets. The instructions are intentionally readable, not a security boundary.

## Research and Outputs

Choose coverage to find as much relevant work as possible, or precision for a shortlist of the closest papers. If the prompt is unclear, the agent asks first. Both start with the curated ChipSeeker library, then supplement meaningful gaps with verified external sources unless you request library-only search. Reports show the chosen objective and label where papers were found. Precision alone does not impose IEEE, recency or measurement constraints.

After the initial screen, `research_output.py review-next --workspace WORKSPACE` produces a prioritized follow-up plan. Record selected checks with `--receipt FILE`, then screen newly found candidates before rendering. Index and external checks have independent limits and outcomes; a missing DOI in the index does not close a publisher or citation check.

For a credential-free offline acceptance run, execute `python skills/chipseeker-ultra-search/scripts/verify_circuit_review.py --parent OUTPUT_PARENT`. It generates paired reports from fixed public metadata and original-paper evidence. The installed Skill includes the same verification command and fixture.

For ambiguous prompts such as `ADC` or `CTLE`, the agent clarifies the intended direction. An explicit all-band or all-technology survey is a valid goal and is divided into branches. Quick trials remain bounded, representative searches.

For a scoped direction, the agent lays out distinct retrieval questions, learns new terms from titles and abstracts, and follows relevant leads. It does not equate paraphrase count, repeated top hits or an API candidate cap with completeness. Paper facts require source evidence; unknown or failed retrievals remain explicit.

The requested path is a parent folder. Each run creates `芯寻_<concise-topic>` containing `结果报告.md`, `结果报告.html` and `检索数据/`. Chinese filenames and report defaults are intentional; the operational instructions are English. The self-contained HTML embeds the ChipSeeker logo and supports offline search, filtering and sorting. Source/PDF links still depend on network connectivity and source access rights. Raw JSON, checkpoints and screening records stay in the data subfolder.
