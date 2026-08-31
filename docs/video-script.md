# Demo video, 4 minutes hard

Record after the deploy is verified. Two terminal windows, one browser.
Nothing is edited: the rubric asks for live execution.

## Before you hit record

```sh
cd ~/Projects/warehouse-sentinel
python3 tools/generate_warehouse.py --out data/clean            # today's window
./tools/load_bigquery.sh clean                                  # start sound
gh issue close 1 --repo lynne-ngendo/warehouse-sentinel         # or use --ignore-suppression
```

Open these tabs, in this order, and leave them open:

1. `https://warehouse-sentinel-745182858091.us-central1.run.app`
2. `https://console.cloud.google.com/run/detail/us-central1/warehouse-sentinel/metrics?project=warehouse-sentinel-2608`
3. `https://github.com/lynne-ngendo/warehouse-sentinel/issues`

Terminal font size up. Window wide enough that the sweep output does not wrap.

---

## 0:00 to 0:25  The problem

Screen: the findings service, showing **All 7 contracts hold**.

> Threshold alerts catch the loud failures. The expensive ones are quiet. If a
> subset of reporting units stops sending data and the remaining units absorb
> the work, the daily total never moves. Every dashboard stays green, and
> nobody finds out for weeks.

## 0:25 to 0:50  What it does

Screen: the architecture diagram.

> Warehouse Sentinel declares seven table contracts and checks them in SQL.
> Detection is deterministic. The model is never asked whether something is
> broken, only what broke and why. So a model that hallucinates cannot invent
> an incident, and a model that is down does not stop detection.

## 0:50 to 1:20  Break it

Terminal 1:

```sh
python3 tools/generate_warehouse.py --out data/coverage_gap --fault coverage_gap
./tools/load_bigquery.sh coverage_gap
```

> Six of forty units go silent. The generator redistributes their volume across
> the units still reporting, so the daily total does not move. That is what
> makes this invisible, and it is deliberately harder to build than just
> deleting rows.

## 1:20 to 2:40  The run — this is the shot that matters

Terminal 1, let it run without cutting:

```sh
SENTINEL_GITHUB_TOKEN=$(gh auth token) python tools/run_agent.py --file-issues
```

Narrate over it while it runs:

> The check layer finds it: six of forty units stopped reporting, fifteen
> percent, while the total held flat. That finding goes to a Gemini 3.5 Flash
> agent on Vertex AI, running on the Agent Development Kit. It queries the
> warehouse itself to test its hypothesis, classifies the failure, and writes a
> query that reproduces it.
>
> Before anything is filed, that query is executed. A diagnosis whose query
> does not run is not shippable.

Wait for the last line, then read it out:

```
issue created: https://github.com/lynne-ngendo/warehouse-sentinel/issues/N
```

## 2:40 to 3:15  It actually acted

Browser: open the issue URL the run just printed. Scroll it slowly.

> Nobody was involved. The reproducing query is in the issue, verified, with
> the row count it returned. The evidence from the check layer is attached.

Terminal 1, run the identical command again:

```sh
SENTINEL_GITHUB_TOKEN=$(gh auth token) python tools/run_agent.py --file-issues
```

> Run it again and it suppresses instead of duplicating. The fingerprint lives
> in the issue body, so GitHub holds the state. A six-hourly sweep files once.

## 3:15 to 3:45  Proof it runs on Google Cloud

Browser: the Cloud Run console tab, then the live service URL, then refresh it
so the failure shows.

> The service is on Cloud Run, the warehouse is BigQuery, the model is Gemini
> 3.5 Flash on Vertex AI. There is no model API key anywhere in this project:
> the service account authenticates directly. The only credential is a GitHub
> token scoped to issues on one repository.

## 3:45 to 4:00  Close

> Seven contracts, four failure modes, each caught by exactly one check, and no
> false positives on clean data. It finds what alerts miss, works out why, and
> files it. Thank you.

---

## After recording

1. Upload to YouTube. Set visibility **Public**, not unlisted.
2. Wait for processing to finish.
3. Open the link in a **private window**. Visibility and processing are
   independent: a video can look public in Studio and 404 to a logged-out
   viewer.
4. Only then paste the link into Devpost and submit.
