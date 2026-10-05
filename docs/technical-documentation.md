# agent-core: technical documentation

- Repo: https://github.com/pulso-factored/agent-core (local checkout: `C:\Users\JUAN\Documents\factored\agent-core`, stale feature branch; NOT used as source)
- Source of truth for this document: `origin/main` at commit **`eb33f5cc771618008600f4cd655682295c03d742`** ("Merge pull request #81 from pulso-factored/fix/classifier-input-key"), read from a worktree. The only delta from the first-pass commit `437824f` is a change to language thresholds in the local state kit and reference compose (`scripts/serve_state.py`, `deploy/compose/docker-compose.yml`, one test). Contract version: `SCHEMA_VERSION = "1.7.0"` (`contracts/VERSION`, `agent_core/domain/version.py`).
- Sibling repo read for consistency: `tool-service` `origin/main` (`registry/tools/*.yaml`, `contracts/`, README).
- Cross-checked against: `C:\Users\JUAN\Documents\factored\runbook-e2e-real.md` (written against `4fa1a8d`, older than the commit above), the repo `CLAUDE.md`, `README.md`, `docs/serve-env.md`, `docs/serve-readiness.md`, ADRs 0003/0018/0019/0020/0023/0024/0025/0026 and the module specs `docs/specs/motor/`.
- Convention: statements marked **[not verified]** were not confirmed against code or by execution in this pass. Everything else was read in the code or docs at the commit above. Nothing was executed (no tests, no containers) while writing this document.
- Contents: 1 Purpose and role; 2 Architecture (system context, components, turn cycle); 3 Agents and modules; 4 Contracts; 5 Integration; 6 Configuration; 7 Run, test, Docker, deploy; 8 Observability, evaluation, calibration; 9 Known limits; 10 Verification status; **11 Registry (in depth)**.
- Language note: the repo's code comments, specs and ADRs are written in Spanish; identifiers are quoted as they appear in code.

---

## 1. Purpose and role

`agent-core` is a **decision engine that runs agents described as versioned data**. It receives a message, understands what the person wants, follows a statically validated script (a *flow*), acts behind safety rails (confirm, act, verify), and leaves an auditable, hash-chained trail (README, `CLAUDE.md`). The cycle is Understand, Decide, Act, Verify, Escalate. It is business-agnostic: an agent, flow, policy or template is data; the engine only interprets it. Customer-facing agents (reception, disputes, queries) and internal ones (advisor copilot, agent builder) run on the same engine.

Guarantees stated by the repo: permissions enforced at the tool layer (the subject comes from the credential, never from the body or the model); every write goes confirm -> act -> verify with an idempotency key and crash recovery; business rules are protected policies outside the model; no `full`-view PII reaches models, logs or events; generated text is validated against facts (citations, figures, PII, language) before leaving; escalation hands off a structured packet; each run leaves a hash chain of events that can be replayed.

### Relationship with the other services

| Service | Relationship | Evidence |
|---|---|---|
| **support-platform** (other repo) | Calling application. Calls `agent-core` `/v1` API with signed identity (Ed25519 JWS) and, for advisors, a signed delegation (`X-On-Behalf-Of`). `agent-core` calls the platform back to check that a delegation grant is still active (`GET {AGENTCORE_GRANTS_URL}/api/v1/internal/grants/{ref}`). The platform issues the keys whose public halves `agent-core` loads (`identity-keys.json`, `staff-keys.json`). The platform also consumes the outbound events and the registry API (proposals, approvals). | `agent_core/adapters/grants.py`, `docs/serve-env.md` §6, runbook §2 |
| **tool-service** (other repo) | Standalone HTTP service implementing the data side of tools (`POST {url}/v1/tools/{id}/execute`). `ToolDef`s (risk class, auth level, idempotency, read-back) stay in the registry; the service implements them by name. `agent-core` sends verified claims (never a token) plus engine-controlled `bound_params`. | ADR 0025, `agent_core/adapters/tools/http_executor.py` |
| **llm-gateway** (other repo `pulso-factored/llm-gateway`) | Standalone service that talks to LLM providers (OpenRouter in the runbook). `agent-core` is a consumer: `HttpLLMGateway` calls `POST /v1/generate`, sending the `Prompt` and `ModelProfile` from the registry on every request (the service keeps no state). | ADR 0024, `agent_core/adapters/llm/http_gateway.py` |
| **data-pipeline** (other repo) | Not called at runtime. It publishes the datasets read by the tool-service and the field-classification catalog (`field_classification.json`) that `agent-core` loads from a file; `scripts/serve_state.py --data-pipeline <path>` builds local state from it. | `deploy/compose/README.md`, runbook §2 step 3 |
| **JEV** (external, `https://api.typesafe.ai`, `/v1/systemone`) | Decision-model provider used by the `understand-turno` model. API key required in production. | `agent_core/decision/providers/jev_http.py`, `docs/serve-env.md` |
| **infra** (other repo) | Owns Terraform; `agent-core` owns the Dockerfile and the reference compose (`deploy/compose`). Infra must run `migrate`, `sweep` and `relay` as separate processes. | `docs/serve-env.md` §7, `docs/serve-readiness.md` §4 |

```mermaid
flowchart LR
    subgraph users[People]
      C[Customer]
      A[Advisor / analyst]
      S[Supervisor / approver]
    end
    SP[support-platform<br/>API + UI]
    subgraph AC[agent-core]
      API["/v1 runtime API<br/>/v1/registry, /v1/export"]
      ENG[Turn engine + registry]
    end
    PG[("Postgres<br/>state, audit chain,<br/>outbox, registry")]
    TS[tool-service<br/>gold_restricted dataset]
    LG[llm-gateway]
    OR[LLM provider<br/>e.g. OpenRouter]
    JEV[JEV<br/>api.typesafe.ai]
    DP[data-pipeline<br/>datasets + field catalog]
    SNS[SNS topic<br/>outbound events]
    OTEL[OTLP backend<br/>Phoenix / Langfuse]
    C --> SP
    A --> SP
    S --> SP
    SP -->|"signed principal + delegation"| API
    API --> ENG
    ENG --> PG
    ENG -->|"POST /v1/tools/id/execute"| TS
    ENG -->|"POST /v1/generate"| LG
    LG --> OR
    ENG -->|"POST /v1/systemone"| JEV
    ENG -->|"GET /api/v1/internal/grants/ref"| SP
    DP -.->|"publishes datasets"| TS
    DP -.->|"field_classification.json (file)"| ENG
    PG -->|"outbox via agentcore relay"| SNS
    ENG -.->|"OTLP traces"| OTEL
```

Design principle (`docs/00-descomposicion-y-repos.md`): each system consumes and produces explicit contracts and does not know the internals of the others. Amendment of 2026-09-29 (ADR 0017/0018): the "agent-registry" idea became the in-repo module `agent_core.registry` with Postgres as the single source of truth; YAML is only an import/export format.

Stack (ADR 0001, `CLAUDE.md`): Python 3.12 (`>=3.12,<3.13`), FastAPI, Pydantic v2, Postgres 16, `uv`, docker-compose. No Redis, no vector DB. S3 (registry blobs) and SNS (outbox relay) are opt-in (ADR 0023).

---

## 2. Architecture

### 2.1 Layout and module boundaries

```
agent_core/
  domain/  ports/      M0: types, nodes, events, errors, JCS canonicalisation, ports
  flows/               M1  static validation of flows (rules G0-xx, MT-xx, AG-xx)
  interpreter/         M2  node interpreter (collect, decide, rule, tool, write, confirm, respond, knowledge, agent, suggest, transfer, ...)
  actions/             M3  write protocol confirm -> act -> verify
  turn/                M4  turn cycle (TurnEngine)
  decision/            M5  DecisionModel + Understand, providers: jev, classifier, rule, llm_structured; calibration
  guards/              M6  input guards: language, size, injection
  views/               M7  data views and tokenisation (model / audit / full), field fingerprints
  response/            M8  response generation and validator (numbers, PII, citations, language)
  api/                 M9  HTTP API /v1, access gate, rate limits, problem+json
  handoff/             M10 escalation and handoff packets
  audit/               M11 audit chain, transcript, replay (fixture / audit modes)
  knowledge/           M12 knowledge nodes (read implemented; navigate out of scope)
  registry/            unit 2: entities, proposals, evaluation gate, publish/promote, lineage
  outbound/            public outbound event contract (separate from M0)
  relay/               outbox -> event bus (SNS)
  adapters/            real adapters (clock, ids, keys, JWS identity, Postgres UoW/audit/transcript, HTTP LLM, HTTP tools, grants, SNS)
  composition/         composition root: builds the engine, evaluator, `serve`, registry service, CLI pieces
  cli.py               `agentcore` entry point
agent_telemetry/       OpenTelemetry spans and correlated JSON logs
testing/               in-memory fakes of every port, demo scripts, calibration tooling
contracts/             generated JSON Schema / OpenAPI (do not edit by hand)
deploy/compose/        reference compose for `serve`
scripts/               e2e helpers, `serve_state.py`, `sync_tool_contract.py`
```

Boundaries are enforced in CI by `import-linter` (`.importlinter`): a module may import only `agent_core.domain`, `agent_core.ports` and the public `__init__` of the modules permitted for it (matrix in `docs/specs/motor/00-indice.md` §3). `agent_core.composition` and `agent_core.cli` are the composition roots: they may import everything and nobody may import them. M4 (`turn`) may not import `agent_telemetry`; telemetry goes through a local port (`TurnTelemetry`).

Hard rules (`CLAUDE.md`): no `datetime.now()`, `time.time()`, `uuid4()`, `random`, `secrets` (everything through the injected `Clock` / `IdSource`, enforced by `ruff`); determinism (same ports and clock produce the same events, basis of replay); money with `Decimal`, never `float`; all JSON enters through `agent_core.domain.loads` and canonicalisation uses `canonical_bytes` (JCS); synthetic data only in fixtures and tests; nothing in `full` view goes to models, logs or events.

```mermaid
flowchart TB
    subgraph edge[Edge]
      API[api M9<br/>gate, limits, problem+json]
      REGHTTP[registry http<br/>/v1/registry]
      EXP[export http<br/>/v1/export]
    end
    subgraph core[Engine]
      TURN[turn M4<br/>TurnEngine]
      GUARD[guards M6]
      DEC[decision M5<br/>understand + providers]
      INT[interpreter M2<br/>node handlers]
      ACT[actions M3<br/>confirm-act-verify]
      RESP[response M8<br/>validator]
      VIEWS[views M7<br/>tokenisation]
      HAND[handoff M10]
      AUD[audit M11<br/>chain, transcript, replay]
      KNOW[knowledge M12]
      FLOWS[flows M1<br/>static validation]
    end
    subgraph reg[Registry unit 2]
      RSVC[RegistryService<br/>proposals, gate, publish]
      EVAL[evaluation<br/>ScenarioEvaluator, gate]
      RSTORE[("reg_* tables / blobs")]
    end
    COMP[composition + cli<br/>composition root]
    TEL[agent_telemetry]
    API --> TURN
    TURN --> GUARD
    TURN --> DEC
    TURN --> INT
    TURN --> HAND
    TURN --> AUD
    INT --> ACT
    INT --> RESP
    INT --> DEC
    INT --> KNOW
    DEC --> VIEWS
    RESP --> VIEWS
    AUD --> VIEWS
    REGHTTP --> RSVC
    RSVC --> FLOWS
    RSVC --> EVAL
    RSVC --> RSTORE
    EVAL -.->|"builds an engine via harness"| COMP
    COMP -.->|"wires everything"| core
    COMP -.-> TEL
    EXP --> RSVC
```

Arrows are the main dependency directions (simplified; the exact matrix is in `docs/specs/motor/00-indice.md` section 3 and `.importlinter`). The engine reads releases only through `RegistryPort`; the registry package is imported only by `composition`. The registry lifecycle and the runtime read path are drawn in section 11 (diagrams 11.1 and 11.2).

### 2.2 Ports and adapters

Ports live in `agent_core/ports/` (clock, ids, registry, tools, authz, identity, keys, uow, audit, transcript, llm, knowledge, directory, publisher, export, costs). Every port has an in-memory fake in `testing/fakes/` that passes the same contract suite (`tests/contracts/`) as the real adapter. Real adapters at this commit:

| Port | Real adapter |
|---|---|
| `ToolExecutor` | `HttpToolExecutor` (`adapters/tools/http_executor.py`), plus engine-served tools (`composition/engine_tools.py`) and builder tools (`composition/builder_tools.py`) |
| `LLMGateway` | `HttpLLMGateway` (`adapters/llm/http_gateway.py`); `LLMAgentPort` for the `agent` node |
| `IdentityVerifier` | `JwsIdentityVerifier` + key files (`adapters/jws_identity.py`, `identity_keys.py`) with hot reload |
| `AuthzPort` | `policy_authz` (`adapters/policy_authz.py`), fails closed |
| `UnitOfWork` / audit / transcript | Postgres (`adapters/postgres_uow.py`, `postgres_audit.py`, `postgres_transcript.py`) |
| `grant_active` | `http_grant_active` (`adapters/grants.py`) |
| `EventPublisher` | `SnsEventPublisher` (`adapters/sns_publisher.py`) |
| `KeyProvider` | `EnvKeyProvider` (`adapters/env_keys.py`) |
| Registry store / blobs | Postgres (`registry/postgres/`), optional S3 blobs (`registry/s3_blobs.py`) |

### 2.3 Turn cycle

`TurnEngine.handle_turn` (`agent_core/turn/engine.py`, spec `docs/specs/motor/m04-ciclo-del-turno.md` §3.1). One unit of work per turn; the two commits per write are done by M3.

```mermaid
sequenceDiagram
    autonumber
    participant P as support-platform
    participant API as api (M9)
    participant T as TurnEngine (M4)
    participant G as guards (M6)
    participant U as Understand (M5)
    participant I as interpreter (M2)
    participant A as actions (M3)
    participant TS as tool-service
    participant LG as llm-gateway
    participant DB as Postgres (state, audit chain, outbox)

    P->>API: POST /v1/sessions/{id}/turns (Bearer JWS, X-On-Behalf-Of?)
    API->>API: verify signature/expiry, delegation (grant check), principal match, rate limit
    API->>T: handle_turn(principal, on_behalf_of, TurnInput)
    T->>DB: dedupe by client_turn_id, load run, acquire turn lease
    T->>T: release revoked? inactivity abandon? recover pending write (-> verify)
    T->>G: language, size, injection
    alt confirm answer present
        T->>T: resume = confirm_answer (no Understand call)
    else
        T->>U: model-view text + state
        U-->>T: command, flow, interrupt, slots (calibrated, thresholded)
    end
    T->>T: global handlers (interrupt, handoff, cancel, clarify, ...)
    T->>I: advance(state, ctx, resume)
    I->>TS: read / write tools (verified claims + bound_params)
    I->>A: confirm -> act -> verify (idempotency key)
    I->>LG: respond(generate), agent, suggest, decide(llm_structured)
    I-->>T: StepOutcome (messages, awaiting, transfer, escalation, end)
    T->>T: close / escalate (handoff) / transfer to another agent in the same turn
    T->>DB: record transcript + response_emitted, turn_completed, ONE commit (state_version+1, events, outbox, result)
    T-->>API: TurnResult
    API-->>P: JSON (trace_id)
```

Notable behaviours documented in M4 §3.1:
- Dedup by `client_turn_id`; a turn lease prevents concurrent turns (`409 turn_in_progress`); closed run gives `410 run_closed`.
- A revoked release escalates (`release_revoked`) without running nodes; inactivity beyond the TTL closes the run `abandoned` and returns 410 without processing the message.
- Recovery: a write left pending after a crash repositions the flow at its `verify` node before processing the new message.
- Understand is skipped when the request carries a confirm token/answer.
- Agent-to-agent transfer (ADR 0021) happens in the **same turn**: the destination run is created, `run_started{origin}` and `transfer_received` are emitted and both runs, their event chains and the result are committed in one transaction. The session lineage links both runs by hash.
- `start_run` is idempotent per `(principal.key, idempotency_key)`; same body returns the stored `RunResult`, a different body gives `409 idempotency_conflict`.
- Task-mode runs (no user conversation) advance to a terminal state in `start_run`; in that case `RunResult.suggestions` carries the copilot output (see §3.5).

### 2.4 Run state ownership

`RunState` is a single record, but each part has exactly one module that writes it (`00-indice.md` §5): M4 owns run identity, status, awaiting and pending intents; M2 owns `active_flow`, `slots`, `facts`, `decisions`, budgets; M3 owns `actions`; M7 owns `token_map`; M12 owns `pages`. Postgres enforces one open run per session (index `runs_one_open_per_session`, per README; **[not verified]** against a live Postgres in this pass).

---

## 3. Agents and modules

Agents are data in the registry (`Agent` entity: mode `conversational` or `task`, `entry_flow`, `invocable_by`, `min_auth_level`, `subject_kinds`, `supported_locales`, `tools_allowed`, `budgets`, `templates`, `understand`, `max_clarifications`, ...). The fixtures that ship in `tests/fixtures/registry-e2e/` (used by the e2e runbook) define the following agents; they are fixtures, not part of the engine.

| Agent | Mode / invoked by | Role | Source |
|---|---|---|---|
| `recepcion` | conversational / customer | Reception: understands the problem, reads the `atencion-cliente` directory, chooses a specialist with the `elegir-especialista` classifier and transfers | `registry-e2e/agents/recepcion@1.0.0.yaml`, `flows/recepcion@1.0.0.yaml` |
| `disputas` | conversational / customer | Dispute flow `disputa-cargo`: find the charge, `match-cargo` rule, `seleccionar`, `convertir_moneda`, confirm, `radicar_pqr`, verify with `obtener_pqr`, generated response | `registry-e2e/flows/disputa-cargo@1.0.0.yaml` |
| `consultas` | conversational / customer | Case/PQR status queries. Verified on `main`: `consulta-pqr` reads `leer_pqr_cliente@1` (limit 50), decides with the rule model `match-radicado@1` (`compare_on: full`) and picks the case with the engine tool `seleccionar_caso@1` | `registry-e2e/flows/consulta-pqr@1.0.0.yaml` |
| `copiloto-asesor` | conversational / advisor | Read-only advisor copilot (ADR 0019 §7): answers advisor questions about the customer using read tools | `registry-e2e/agents/copiloto-asesor@1.0.0.yaml`, `flows/asistir@1.0.0.yaml` |
| `copiloto-sugerencias` | task / advisor | Structured suggestions for the advisor (ADR 0026) | `tests/fixtures/copiloto-sugerencias/` |
| `constructor-chat` | conversational / builder | Agent builder (improvement engine) chat; writes drafts to the registry | `registry-e2e/agents/constructor-chat@1.0.0.yaml`, `flows/construir@1.0.0.yaml` |

### 3.1 Understand (`understand-turno`) and decision models (M5)

- `agent_core/decision/` implements `DecisionModel` entities (`DecisionModelDef`) with pluggable **providers**: `jev` (`providers/jev.py`, HTTP transport `jev_http.py`), `classifier` (`providers/classifier.py`), `rule` (`providers/rule.py`), `llm_structured` (`providers/llm_structured.py`). The provider contract (ADR 0005) treats JEV as one provider among others.
- `UnderstandService` (`decision/understand.py`) is "one more DecisionModel" with a closed schema built per release: `command` (enum), `flow` (enum of the release's flows), `interrupt` (enum of the release's interrupts), plus free-form `slots` (claimed, **not validated** by Understand; M4/M2 must not treat them as facts). It returns `UnderstandResult` with calibrated probabilities `p_cal` and an `above_threshold` mark per calibrated field. A second optional call (`slots_model_ref`, `llm_structured`) extracts slots.
- Fixture model `understand-turno@1.0.0` (`registry-e2e`): provider `jev`, model `jev-1.13.0`, `timeout_ms: 10000`, three questions (`command`, `flow`, `interrupt`) with per-value criteria in Spanish; `calibrated_fields: [command, flow, interrupt]`; `thresholds_from: cal-transfer-demo`. Commands seen in the criteria: `start_flow`, `continue`, `affirm`, `deny`, `clarify`, `cancel`, `handoff`, `out_of_scope`, `interrupt`.
- Global handlers (M4 §3.2) act on the command and on `below_threshold`: below-threshold values are not trusted (clarification, abstention or escalation per agent config, `max_clarifications`, `on_clarify_exhausted`).
- Per-turn caps: `max_model_calls_per_turn`, `max_tokens_per_run`, `max_cost_per_run`, `max_wall_ms_per_turn` (agent `budgets`).

### 3.2 Classifier

- Provider `classifier`: TF-IDF + logistic regression evaluated in pure Python (format `tfidf-logreg-v1`, JSON with `data_hash`, `vocab`, `idf`, `classes`, `coef`, `intercept`). Deterministic: NFKC lowercase, `\w+` tokens, L2-normalised, stable softmax, ties broken by value. `config.text_from` names which input key holds the text.
- Used by `elegir-especialista@1.0.0` (reception chooses `disputas` / `consultas`) with `config: {artifact: sintetico, text_from: slots.problema}`.
- Artifacts are loaded from `AGENTCORE_CLASSIFIER_ARTIFACTS_DIR` (`composition/artifacts.py`); the real artifact is to be produced by the data team. At this commit the artifact used locally is **synthetic** (`testing/serve_classifier.py`), declared in its `limitations`; the runbook records that the hand-made vocabulary misrouted some messages before it was widened. `main` has `tests/composition/test_serve_classifier.py` and history entries for the synthetic classifier (commits `6d67d5a`, `15f8a57` with Portuguese tokens), so the widening is in; its quality against real messages remains unmeasured.

### 3.3 Calibration

- `agent_core/decision/calibration/` (isotonic regression `isotonic.py`, metrics `metrics.py`: ECE, macro-F1, coverage, precision/recall at threshold; threshold selection `thresholds.py`; `calibrate.py` producing a `CalibrationArtifact`; report in `report.py`). `python -m agent_core.decision report --artifact <json> [--events <jsonl>] [--format md|json] [--out <path>]` prints per-language / per-provider metrics.
- `calibrate` is a pure function of its content (no clock, no ids): same split and provider outputs give the same artifact (`split_hash`, `run_id` by hash). Base language is `es`; Portuguese examples are synthetic (ADR 0012) and, below the minimum (200 dev examples), the Spanish calibration is copied (`testing/calibration/README.md`).
- Thresholds are referenced by `thresholds_from: <calibration id>`; served from `AGENTCORE_CALIBRATION_DIR`. A missing calibration means "nothing passes" (threshold 1.0), so `serve` refuses to start in production without the directory.
- The calibration of `understand-turno` in the repo is **synthetic/demo** (`testing/calibration/`): labelled set written by hand, recorded JEV outputs in `recorded/*.jsonl`, artifacts `cal-02942767f4c10da2` (current model) and `cal-66d5b362c5386147` (strict variant). Reported figures (Spanish, closed `test` set, n = 166): current model accuracy 92.8 % / coverage 83.1 % / precision of accepted 97.8 % / `start_flow` coverage 35.7 %; strict variant 94.6 % / 97.6 % / 96.9 % / 100 %. Known limits listed there: `interrupt` is not calibratable (single-value enum, JEV always answers `fraude` at 1.0), some labels are debatable, the strict criterion was written after seeing errors in the full set (possible overfitting; a holdout check on 42 new messages showed 0/24 foreign banking requests marked `start_flow` for the strict variant vs 22/23 for the current one). These figures are copied from the repo README and were not re-run here.

### 3.4 Other engine modules (short)

- **Guards (M6, `agent_core/guards/`)**: language detection (lingua; thresholds file `AGENTCORE_LANG_THRESHOLDS`; without it the language never switches by detection), input size (`max_input_chars`), prompt-injection ruleset (`injection_ruleset` in the release); `injection_flagged` sets `degraded=true` for the turn.
- **Views and tokenisation (M7, `agent_core/views/`)**: three views of data (`model`, `audit`, `full`), field classification (`field_class`, `tag`, `quasi`), keyed fingerprints, token vault. Unclassified fields default to `pii_direct`. ADR 0008.
- **Response (M8, `agent_core/response/`)**: `respond` generates through the gateway and validates numbers, PII, citations, language (`check_numbers.py`, `checks.py`, `validate.py`); on failure falls back to a template or escalates (`response_failed`).
- **Actions (M3)**: write protocol; risk classes include `write_draft` (ADR 0019 §5: builder-only, act -> verify without confirm), `write_reversible`, `write_irreversible`, `money_movement`.
- **Handoff (M10)**: escalation as an outbound event with a structured packet (ADR 0013); `GET /v1/handoffs/{ref}` and `POST /v1/handoffs/{ref}/resolution`.
- **Audit (M11, `agent_core/audit/`)**: hash-chained events, transcript with keyed hashes, replay (`fixture` and `audit` modes), export.
- **Knowledge (M12)**: `read` implemented (node `knowledge`, `knowledge_from`/`purpose`, G0-17..G0-21); `navigate` not built. README still says "pending"; the index says `read` was implemented 2026-09-30, and the `knowledge/` package exists on `main`.

### 3.5 Advisor copilot and suggestions

- **`copiloto-asesor`** (ADR 0019): conversational, read-only, principal `advisor` with a delegation. Uses the `agent` node (read and compute only; output marked as model-generated and barred from feeding writes, `rule`, `verify` by rule G0-22). Engine-served tools `obtener_handoff` and `leer_transcript` read the assistant session named by `input.assistant_session_id` (set by the platform, never by the model); every run in that session must belong to the call's subject, otherwise `denied`. Read tools `leer_productos`, `leer_movimientos`, `leer_pqr_cliente` go to the tool-service.
- **`copiloto-sugerencias`** (ADR 0026, status: accepted and implemented in the core; the agent, prompt, escalation policy and eval suite are explicitly **PROVISIONAL**): a `task` agent, one run per suggestion, no memory between runs. New node `suggest` producing a typed list (max 3, ordered `escalate`, `reply`, `tool`, `action`): `reply` (draft for the customer, M8-validated), `tool` (a read the advisor might run, `id@MAJOR`), `action` (prepared write, `executable` always `false`; `actions_allowed` is empty today), `escalate` (only reachable through a `rule`, rule G0-28; the model only drafts `motive_draft`, in Spanish). Output travels only in `RunResult.suggestions` (only when the run ends `completed`) and the audit event `suggestions_produced` (counters, never text). An empty list is a valid result. The flow input is flat (no object/null slots). Customer data enters the model wrapped as `untrusted_text`. Documented gaps in the ADR: M6 injection guard does not run over `input.turnos` in task runs; the eval suite covers four of seven required cases (injection, other-customer, Portuguese missing); cost cap (0.10 per run, 4 model calls, 20 s) is provisional.

### 3.6 Improvement engine (builder) and the registry

- The "improvement engine" is the **builder** principal plus the registry proposal cycle (ADR 0018/0019/0020). Agents `constructor-chat` (conversational) and a `task` twin share flows, tools and prompts. The registry credential: `type=builder`, `roles=["constructor"]`, no `attrs.actor`, `auth.level=session`, issued by the staff issuer; only its public key is loaded by `serve` (`--staff-keys`).
- Proposal lifecycle: `draft -> validated -> candidate -> evaluated -> approved -> published` (side states `rejected`, `abandoned`, `stale`). Freezing produces a candidate identified by hash; evaluations and approvals are bound to the hash. Publishing is one transaction (inserts versions, creates the release, points `staging`); promoting to `prod` is a separate step; revocation is immediate.
- Server-side role rules (`agent_core/registry/roles.py`, documented in `docs/serve-env.md` §8): the builder may create proposals, write drafts, validate, freeze, reopen, evaluate and read; approve, reject, publish, promote, revoke and seed import return **403 `forbidden_role`** (they require a human with `aprobador`/`admin` and `step_up`). Covered by `tests/composition/test_serve_engine_builder.py` per the doc (not re-run here).
- Evaluation gate (ADR 0020): each agent declares metrics in `Agent.metrics` (declarative DSL over a closed event catalogue `engine.*`/`registry.*`, validated by M1 rules MT-01..MT-06). Roles: `guardrail` (zero tolerance), `gate` (no worse than base within the noise margin and above a floor), `monitor`. "Double yardstick": a candidate must pass the base release's unchanged suite and its own new suite; loosening the yardstick is flagged `yardstick_loosened` and needs a separate human approval. `eval_suite` is a versioned registry entity; the scenario source `scripted` is enabled, `dataset` is designed but disabled. Evaluation code: `agent_core/registry/evaluation/` (`evaluator.py`, `gate.py`, `guardrails.py`, `local_sandbox.py`, `metric_eval.py`, `scoring.py`, `yardstick.py`). Tools in evaluation are doubles by design (`local_sandbox.py`).
- Evaluation DB is separate from the main DB (`AGENTCORE_EVAL_DSN`).
- The full registry description (format, schema, lifecycle, gate, runtime read path, how to add or change an entry) is in section 11.

---

## 4. Contracts and schemas

`contracts/` is **generated** (`uv run agentcore contracts`; `--check` in CI). Contents at the pinned commit:

| Path | Contents |
|---|---|
| `contracts/VERSION` | `1.7.0` (equals `SCHEMA_VERSION`) |
| `contracts/schemas/*.json` | JSON Schema of the M0 domain types: `Agent`, `Flow`, all node types and configs (`CollectNode`, `DecideNode`, `RuleNode`, `ToolNode`, `WriteToolNode`, `ConfirmNode`, `RespondNode`, `KnowledgeNode`, `AgentNode`, `SuggestNode`, `TransferNode`, `EscalateNode`, `AwaitApprovalNode`, `SubflowNode`, `VerifyNode`, `EndNode`), run/turn types (`RunState`, `RunInput`, `RunResult`, `TurnInput`, `TurnResult`), every engine event and its payload (`RunStarted`, `TurnStarted`, `CommandEmitted`, `NodeEntered`, `RuleEvaluated`, `ToolCalled`, `DecisionMade`, `ActionConfirmed/Dispatched/Verified/Cancelled`, `ResponseEmitted/Failed`, `SuggestionsProduced`, `Escalated`, `HandoffResolved`, `RunTransferred`, `TransferReceived/Rejected`, `InjectionFlagged`, `AccessDenied`, `KnowledgeRead`, `StepUpRequested`, `TurnCompleted`, `RunClosed`, `ExpiryEvaluated`, `AgentStep`, `CommandEmitted`), plus `Principal`, `Policy`, `Prompt`, `ModelProfile`, `DecisionModelDef`, `MetricDef`, `Release`, `ToolDef`, `ProblemCode`, `TransferPacket`, `TransferContract`, `ActionSuggestion`/`ReplySuggestion`/`ToolSuggestion`/`EscalateSuggestion`, etc. |
| `contracts/openapi.json` | Runtime API (info version 1.7.0): `POST /v1/runs`, `GET /v1/runs/{run_id}`, `GET /v1/runs/{run_id}/transcript`, `POST /v1/sessions/{session_id}/turns`, `GET /v1/sessions/{session_id}/lineage`, `GET /v1/handoffs/{handoff_ref}`, `POST /v1/handoffs/{handoff_ref}/resolution` |
| `contracts/registry-openapi.json` + `contracts/registry/*.json` | Registry API and body/model schemas (proposals, drafts, approvals, releases, aliases, pause state, eval runs, lineage, registry events) |
| `contracts/events/OutboundEvent.json`, `events/catalog.json` | **Public outbound event contract** for other services (envelope + closed list of types): `run.started`, `run.closed`, `run.transferred`, `handoff.created`, `handoff.resolved`, `release.published`, `release.promoted`, `release.revoked`. Spec: `docs/specs/2026-10-02-eventos-salientes-design.md`. |

Stable surfaces and compatible migrations: ADR 0022; test `tests/test_stable_surface.py`.

Stable problem codes (`ProblemCode`, with HTTP status): `credentials_invalid` 401, `principal_expired` 401, `subject_forbidden` 403, `agent_forbidden` 403, `version_pin_forbidden` 403, `delegation_expired` 403, `delegation_mismatch` 403, `principal_mismatch` 403, `not_found` 404, `turn_in_progress` 409, `handoff_already_resolved` 409, `idempotency_conflict` 409, `idempotency_in_progress` 409, `run_closed` 410, `invalid_request` 422, `payload_too_large` 413, `rate_limited` 429, `cost_budget_exceeded` 429, `internal_error` 500, `identity_unavailable` 503. Errors are `problem+json` carrying a `trace_id`.

Rule from `CLAUDE.md`: if an M0 type changes, regenerate `contracts/` and flag it as an interface change for all modules.

---

## 5. Integration

### 5.1 With the support platform

- **Runtime API** (`agent_core/api/app.py`): the platform sends `Authorization: Bearer <signed principal>` (Ed25519 JWS; `IdentityVerifier`) and, for advisors acting on a customer case, `X-On-Behalf-Of: <signed delegation>`. The gate verifies signature and expiry, delegation, principal match and then rate limits (`LimitGuard`: sliding window per principal, daily USD budget). `POST /v1/runs` requires an idempotency key (header) and body `{agent, subject?, input?, lang?}`; `POST /v1/sessions/{id}/turns` body `{text, channel, lang?, client_turn_id, confirm?}` (text or confirm required). The subject comes from the credential/delegation; it is never an argument to a tool.
- **Grant check**: for each call with a delegation `agent-core` calls the platform (`GET {AGENTCORE_GRANTS_URL}/api/v1/internal/grants/{ref}` with a shared bearer, answer `{"active": bool}`; positive answers cached `AGENTCORE_GRANTS_CACHE_TTL_S`, default 5 s; fails closed; platform unreachable raises `503 identity_unavailable`). Note from the runbook: `AGENTCORE_GRANTS_URL` is the **base URL** only; the adapter appends the path.
- **Keys**: platform-generated public keys go into `identity-keys.json` (customers/advisors) and `staff-keys.json` (staff/builder credentials); format in `docs/serve-env.md` §6; reloaded every `AGENTCORE_KEYS_RELOAD_SECONDS` (default 5) without restart.
- **Registry API** (`/v1/registry`, mounted only with `--registry-api`/`AGENTCORE_REGISTRY_API=1`): proposals (create, draft, validate, freeze, reopen, evaluate, approve, reject, publish), aliases (promote, read), agent pause/resume, versions, entities, releases (get, diff, revoke), run lineage. Credential: staff issuer.
- **Export API** (`/v1/export`, staff credential with role `exporter`): `GET /v1/export/runs?after=<run_seq>`, `GET /v1/export/runs/{run_id}/events?after=<seq>`, `GET /v1/export/registry-events?after=<n>`, paginated (`limit` max 500, `next_after`), for the ingestion pipeline.
- **Outbound events** reach other services through the Postgres outbox, delivered by `agentcore relay` to an SNS standard topic (ADR 0023: at-least-once; consumer dedupes by `event_id`; message attributes `event_type`, `event_id`, `source`, `spec_version`).
- **Copilot suggestions**: the platform calls `POST /v1/runs` with agent `copiloto-sugerencias@prod` (platform setting `CC_COPILOT_SUGGESTIONS_AGENT`, per the runbook) and reads `RunResult.suggestions` from the response. Platform-side behaviours (stage gates, `copilot_unavailable`) belong to support-platform and were not verified here.

### 5.2 With the LLM gateway

- `HttpLLMGateway.generate` posts to `{AGENTCORE_LLM_GATEWAY_URL}/v1/generate` with `Authorization: Bearer {AGENTCORE_LLM_GATEWAY_TOKEN}`, sending the `Prompt` and `ModelProfile` (including price) from the release's registry entities. The service makes one provider call, prices it from the price sent and returns typed errors, which map one to one to `GatewayErrorKind` (`bad_request`, `unauthorized` and unknown kinds are treated as `unavailable`: misconfigured deployment).
- Client timeout = profile `timeout_s` + 5 s margin so the service's typed `timeout` arrives first.
- Propagates W3C `traceparent` and sends turn correlation (`run_id`, `turn_id`, `session_id`, `release`, `agent`) as `labels`. The span `chat {model}` is emitted by the service.
- URL and token go together or neither; with neither, **every generation falls back to templates** and `serve` warns at startup (plus a non-blocking probe of `/healthz`). Provider keys and `LLM_ENDPOINTS` live in the gateway deployment, not here.
- `agentcore llm-smoke --registry <dir> --profile <id@version> [--n 10]` is a manual smoke test through the gateway (needs a `model_profile` with `endpoint_alias` and `structured: prompted`). The README states this smoke test had not been run against OpenRouter; **[not verified]** whether it has been run since.
- The runbook reports a model alias `xiaomi/mimo-v2.6-flash` (and `mimo-v2.6-pro` as a judge candidate) used in the reference deployment; the model choice is configured in gateway/registry data, not in this repo's code.

### 5.3 With the tool-service

`HttpToolExecutor` posts `{tool, args, bound_params, context{run_id, release, call_id, turn_id, ...}}` plus verified claims to `POST {AGENTCORE_TOOL_SERVICE_URL}/v1/tools/{id}/execute` with a bearer token (ADR 0025). Read statuses: `ok, error, timeout, denied, step_up_required`; write statuses: `ok, denied, uncertain, step_up_required`. A write without `idempotency_key` is a programming error. Nothing logs args, results or claims. The tool contract owned by the tool-service is mirrored in `tests/fixtures/tool-service-contract/` (verified identical, file by file, to `tool-service` `origin/main` `registry/tools/`: 7 tools, provider contract version 1.0.0 on both sides; the tool-service also keeps a provider-neutral OpenAPI in `contracts/tool-provider.openapi.json`); refresh with `uv run python scripts/sync_tool_contract.py [--check] [path-to-tool-service]`; drift is caught by `tests/registry/test_tool_contract_drift.py`.

Tools served by the engine itself (`composition/engine_tools.py`, only in `serve` outside demo mode): `seleccionar` (pick an element of a list by `transaction_id`), `convertir_moneda` (amount x fixed rate from `AGENTCORE_FX_RATES_FILE`, fails closed `fx_unconfigured`; not a market source), `obtener_handoff`, `leer_transcript`. `ENGINE_TOOL_IDS` on `main` is `{seleccionar, seleccionar_caso, convertir_moneda, obtener_handoff, leer_transcript}`; `seleccionar_caso` (pick a case by `case_id`, `source: customer_cases`) is present.

---

## 6. Configuration and environment variables

Source: `docs/serve-env.md` (verified to exist at the commit) and the adapter modules. `serve` validates everything before opening the port; on problems it prints each (variable name, never value) and exits with code 2. Legend: R required in production, C required if the feature is used, O optional, S secret.

### 6.1 Core

| Variable | Kind | Default | Purpose |
|---|---|---|---|
| `AGENTCORE_REGISTRY_DSN` | R, S | none | Main database DSN (engine, audit, registry). `--dsn` exists but exposes the password in the process list. `sweep` also accepts `AGENTCORE_DATABASE_URL` as old alias (ADR 0023). |
| `AGENTCORE_KEYS_FINGERPRINT` | R, S | none | `kid:base64[,kid:base64]`, first is current, >= 32 bytes each. Field fingerprint keys. |
| `AGENTCORE_KEYS_TOKEN_MAP` | R, S | none | Same format; view token map. Rotate by prepending the new key and keeping the old. |
| `AGENTCORE_JEV_API_KEY` | R, S | none | JEV key; in production `serve` does not start without it. |
| `AGENTCORE_SERVE_AGENTS` | O | empty | Agents whose `prod` release is checked at startup (warning only). |
| `AGENTCORE_GIT_SHA` | O | none | Reported by `GET /version`; the Dockerfile sets it from `--build-arg GIT_SHA`. |
| `AGENTCORE_DB_POOL_MAX` | O | `0` | Max connections per process (0 = one per operation). |
| `AGENTCORE_LANG_THRESHOLDS` | O | none | JSON path with language-switch thresholds. |
| `AGENTCORE_FX_RATES_FILE` | C | none | JSON `{"USD":"1","MXN":"0.055"}` for `convertir_moneda`. |
| `AGENTCORE_IDENTITY_KEYS_FILE` (`--identity-keys`) | R | none | Public identity keys file. |
| `AGENTCORE_REGISTRY_API` (`--registry-api`) | O | none | `1` mounts `/v1/registry` and `/v1/export`; requires the next two. |
| `AGENTCORE_STAFF_KEYS_FILE` (`--staff-keys`) | C | none | Staff issuer public keys. |
| `AGENTCORE_EVAL_DSN` (`--eval-dsn`) | C, S | none | Separate evaluation database. |
| `AGENTCORE_KEYS_RELOAD_SECONDS` | O | 5 | Key file reload period (0 disables). |

### 6.2 Real pieces (default factories; replaced with `--<name> module:attribute`)

| Argument | Factory | Needs |
|---|---|---|
| `--tools` | `agent_core.adapters.tools.http_executor:http_tool_executor` | `AGENTCORE_TOOL_SERVICE_URL`, `AGENTCORE_TOOL_SERVICE_TOKEN` (S), `AGENTCORE_TOOL_SERVICE_TIMEOUT_S` (default 10) |
| `--authz` | `agent_core.adapters.policy_authz:policy_authz` | `AGENTCORE_AUTHZ_FIELD_GRANTS_FILE` (O; without it nobody reads any PII field), `AGENTCORE_AUTHZ_BIND_KEYS` (O, default `subject_ref`) |
| `--transcript` | `agent_core.composition.transcript:transcript` | main DB |
| `--calibration` | `agent_core.composition.artifacts:calibration` | `AGENTCORE_CALIBRATION_DIR` |
| `--classifier` | `agent_core.composition.artifacts:classifier_provider` | `AGENTCORE_CLASSIFIER_ARTIFACTS_DIR` |
| `--field-classifier` | `agent_core.composition.classification:field_classifier` | `AGENTCORE_FIELD_CLASSIFICATION_FILES` (comma-separated, last wins) |
| `--grant-active` | `agent_core.adapters.grants:http_grant_active` | `AGENTCORE_GRANTS_URL` (base URL), `AGENTCORE_GRANTS_TOKEN` (S), `AGENTCORE_GRANTS_TIMEOUT_S` (3), `AGENTCORE_GRANTS_CACHE_TTL_S` (5) |

Without `AGENTCORE_ALLOW_DOUBLES=1` (legacy alias `AGENTCORE_ALLOW_DEMO`), `serve` rejects any `testing.*` path. Startup logs `serve mode=production` or `serve mode=demo doubles=a,b`.

### 6.3 External dependencies and operations

| Variable | Default | Purpose |
|---|---|---|
| `AGENTCORE_LLM_GATEWAY_URL` / `_TOKEN` | none | Both or none. |
| `AGENTCORE_BLOB_BUCKET`, `_PREFIX`, `_KMS_KEY_ARN` | none | Registry blobs in S3. With the bucket, `migrate` drops the FK to `reg_blobs`; run `agentcore blobs-backfill` first. |
| `AGENTCORE_EVENTS_TOPIC_ARN` | none | Only for `agentcore relay`. |
| `AGENTCORE_AUTO_MIGRATE` | `1` | `0`: `serve` does not migrate at startup (role without DDL). |
| `AGENTCORE_READY_REQUIRE_LLM_GATEWAY` | `1` | `0`: gateway only informs (`degraded`) in `/readyz`. |
| `AGENTCORE_READY_REQUIRE_TOOL_SERVICE` | `0` | `1`: tool-service blocks `/readyz`. |
| `AGENTCORE_MAX_INFLIGHT` | `AGENTCORE_DB_POOL_MAX // 2` (0 without pool) | Cap on simultaneous `/v1` requests; beyond it 503 with `Retry-After: 1`. Probes never count. A turn holds one connection and requests another, so more concurrent turns than half the pool exhausts it. |
| `AGENTCORE_WORKER_THREADS` | `40` | Threads for sync routes. |
| `AGENTCORE_SHUTDOWN_GRACE_SECONDS` | `25` | Graceful shutdown after SIGTERM; keep below the orchestrator's stop grace period. |
| `AGENTCORE_RATE_MAX_HITS`, `_RATE_WINDOW_SECONDS`, `_RATE_SERVICE_MULTIPLIER`, `AGENTCORE_DAILY_BUDGET_USD` | demo defaults | Per-principal limits (sliding window, daily budget). |

### 6.4 Observability variables

`OTEL_TRACES_EXPORTER` (`otlp` default or `none`), `OTEL_EXPORTER_OTLP_ENDPOINT` / `_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL` / `_TRACES_PROTOCOL` (only `http/protobuf`), `OTEL_EXPORTER_OTLP_HEADERS` / `_TRACES_HEADERS` (S), `OTEL_SERVICE_NAME` (default `agentcore`), `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_TRACES_SAMPLER`/`_ARG`, `OTEL_SDK_DISABLED`, `AGENTCORE_TRACE_CONTENT` (`1` allows prompt/response as span attributes; off by default), `AGENTCORE_TRACE_LANGFUSE` (`1` derives `langfuse.*` attributes; off by default).

### 6.5 File formats

- Identity / staff keys (YAML or JSON): `principal_keys: {kid: base64url of 32-byte Ed25519 public key}`, optional `delegation_keys`. Duplicate, empty or wrong-length keys make `serve` fail closed. A failed reload keeps the last good keys and `/readyz` shows `keys: fail`.
- Field grants: `[["field","purpose"], ...]`.
- Field classification catalog: `{"<table>.<field>" | "<field>": {"field_class","tag","quasi":{"op","width"}}}`; unclassified = `pii_direct`.
- Calibration: `<id>.json` per artifact in `AGENTCORE_CALIBRATION_DIR`; classifier: `<ref>.json` (`tfidf-logreg-v1`) in `AGENTCORE_CLASSIFIER_ARTIFACTS_DIR`.

`.env.example` at the repo root only lists `AGENTCORE_KEYS_FINGERPRINT`, `AGENTCORE_KEYS_TOKEN_MAP`, `AGENTCORE_JEV_API_KEY`, `AGENTCORE_LLM_GATEWAY_URL/TOKEN` and the `llm-smoke` instructions. The reference compose has its own `deploy/compose/.env.example` (contents of that file were not read in full; **[not verified]** its variable list).

---

## 7. Running locally, tests, Docker and deployment

### 7.1 Quick start (no network, no Postgres)

```bash
uv sync --locked
uv run pytest                     # integration tests are skipped without Postgres
uv run agentcore validate tests/fixtures/registry-demo
uv run agentcore replay tests/fixtures/runs/resuelto.yaml --mode fixture \
  --registry tests/fixtures/registry-demo --catalog tests/fixtures/catalogo-datos-prueba.yaml
```

Recorded paths in `tests/fixtures/runs/`: `resuelto`, `cancelado`, `escalado_por_monto`, `uncertain_verify`, `step_up`, `interrupcion`. Transfer demo: `tests/fixtures/registry-transfer-demo`, `tests/fixtures/runs-transfer/transferencia.yaml`.

### 7.2 Quality gates (same as CI, `.github/workflows/ci.yml`)

`uv run ruff check .`, `uv run mypy` (strict; files `agent_core`, `agent_telemetry`, `testing`), `uv run lint-imports`, `uv run agentcore contracts --check`, `uv run agentcore validate` on the demo registries, replay of the six recorded paths, `uv run pytest`. Per-module tests: `uv run pytest tests/mXX`; port contract suites: `uv run pytest tests/contracts`; Postgres integration: `docker compose up -d postgres` then `uv run pytest tests/integration` (CI sets `AGENTCORE_REQUIRE_POSTGRES=1`). Pytest markers: `perf` (only with `AGENT_CORE_PERF=1`), `integration`. Test directories: `m00`..`m12`, `contracts`, `integration`, `composition`, `registry`, `outbound`, `relay`, `gateway_http`, `u05`, `support`. Note from `docs/serve-readiness.md`: GitHub CI did not run (billing); local gates are the ones that count. No test run was done for this document.

### 7.3 CLI (`agentcore`, `agent_core/cli.py`)

| Command | Purpose |
|---|---|
| `contracts [--check]` | Regenerate / verify `contracts/` (schemas, openapi, registry-openapi) |
| `validate <dir> [--json]` | Static validation of an authoring registry (M1) |
| `replay <fixture|run_id> --mode fixture|audit` | Replay a recorded run |
| `record <scenario> --out <file> --registry <dir>` | Record a path |
| `llm-smoke` | Gateway smoke test |
| `registry [--verifier ...] import|export|propose|draft|validate|freeze|reopen|evaluate|approve|reject|publish|promote|revoke|diff|lineage` | Registry operations from the CLI (e.g. seed import needs an `admin` with `step_up`) |
| `serve [--host --port ...]` | Start the HTTP server (M9 + engine, optional registry API) |
| `migrate [--dsn] [--eval-dsn] [--app-role]` | Apply Postgres schemas idempotently with the owner role |
| `sweep --once` | Close expired runs as `abandoned` |
| `relay [--once] [--interval --batch]` | Publish the outbox to SNS; one leader via Postgres advisory lock |
| `blobs-backfill` | Copy `reg_blobs` blobs to S3 |

### 7.4 Docker image

`Dockerfile`: multi-stage, bases pinned by digest (`python:3.12-slim-bookworm`, `uv:0.8`), `uv sync --frozen --no-dev`, non-root user uid 10001, no secrets in the image, `HEALTHCHECK` on `/healthz`, `ENTRYPOINT ["agentcore"]`, default `CMD ["serve","--host","0.0.0.0","--port","8000"]`, `STOPSIGNAL SIGTERM`. Build: `docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t agent-core .`; other commands by overriding the command (`migrate`, `sweep --once`, `relay`). The CI `image` job checks `--help`, uid 10001 and that no `DSN|TOKEN|API_KEY|PASSWORD|SECRET` variable exists in the image environment. Per `docs/serve-readiness.md`: linux/amd64 321 MB and linux/arm64 351 MB (arm64 built and started under QEMU, not tested live). Gotcha recorded there and in the runbook: rebuilding with Docker Desktop cache once returned old code; use `--no-cache` (`docker compose build --no-cache migrate`).

### 7.5 docker-compose

- Root `docker-compose.yml`: only `postgres:16` (user/db `agentcore`, a dev-only password documented in the file as non-secret, bound to `127.0.0.1:5432`), for local tests.
- `deploy/compose/docker-compose.yml` (+ `docker-compose.local.yml`): reference stack that infra translates to EC2. Services described in the README and runbook: Postgres, `migrate`, `serve`, llm-gateway, tool-service; the local overlay publishes Postgres to the host and adds `grants-stub` (a **double** of the platform: every signed delegation is valid). A read-only `deploy/compose/state/` mounted at `/state` must provide `identity-keys.json`, `staff-keys.json`, `calibration/`, `classifier/`, `field-classification.json`, `fx-rates.json`, `field-grants.json`. `uv run python scripts/serve_state.py [--data-pipeline <path>]` creates synthetic state with TEST keys. Verified in `deploy/compose/docker-compose.yml`: services `postgres` (16, DB `agent_runtime`; `initdb/10-eval.sql` creates `agent_eval`), `migrate` (one-shot `agentcore migrate`), `llm-gateway` (consumer map for `agent-core`, endpoint alias `openrouter`), `tool-service` (`TOOL_DATA_DIR=/data`, dataset mounted read-only), `serve` (all `AGENTCORE_*` wiring, `./state:/state:ro`, port `127.0.0.1:${SERVE_PORT:-8000}`, `stop_grace_period: 30s`, depends on migrate, llm-gateway and tool-service). The local overlay adds Postgres on host port `${PG_PORT:-55435}` and `grants-stub`. `deploy/compose/.env.example` lists (names only) `POSTGRES_PASSWORD`, `AGENTCORE_KEYS_FINGERPRINT`, `AGENTCORE_KEYS_TOKEN_MAP`, `AGENTCORE_JEV_API_KEY`, `OPENROUTER_API_KEY`, `GATEWAY_TOKEN_AGENT_CORE`, `TOOL_SERVICE_TOKEN`, `TOOL_DATA_HOST_DIR`, `AGENTCORE_GRANTS_URL` (base URL) and `AGENTCORE_GRANTS_TOKEN`, plus optional image, context, port and pool variables.

```mermaid
flowchart LR
    subgraph host["Docker host: reference compose (project agentcore-serve)"]
      PGc[("postgres:16<br/>agent_runtime + agent_eval")]
      MIG[migrate<br/>one-shot]
      SRV["serve :8000<br/>agent-core image, uid 10001"]
      LGc[llm-gateway :8080]
      TSc[tool-service :8080]
      GS["grants-stub :8099<br/>local overlay only"]
      ST["/state (read-only)<br/>keys, calibration, classifier,<br/>catalog, fx, grants"]
      DATA["/data (read-only)<br/>data-pipeline dataset"]
    end
    SP[support-platform<br/>real grants endpoint]
    OR[OpenRouter]
    JEV[JEV]
    MIG --> PGc
    SRV --> PGc
    SRV --> LGc --> OR
    SRV --> TSc --> DATA
    SRV --> JEV
    SRV -.->|"runbook / prod"| SP
    SRV -.->|"local overlay"| GS
    ST --> SRV
    CLIENT[Client or platform] -->|"127.0.0.1:SERVE_PORT"| SRV
```

The production AWS topology (ECS/EC2, RDS Proxy, S3, SNS, scheduled `sweep`, `relay` as a service) lives in the infra repo and was not verified here; the compose file is the contract infra translates.
- Local stack procedure (verified by the runbook on 2026-10-05, not re-run here): worktrees of the four repos, generate platform keys, build `state/`, copy platform public keys into it, create `deploy/compose/.env`, `docker compose -p agentcore-live -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.local.yml --env-file deploy/compose/.env up --build -d`, check `GET :8001/readyz`, import agents from `tests/fixtures/registry-e2e` with a short-lived staff credential, link simulator customers to real dataset ids. Use a dedicated compose project name (`-p`) to avoid reusing a Postgres volume with a different password. `scripts/e2e/` is a separate, older local E2E kit (verified from file headers): `compose.yml` runs only a dev Postgres on port 55432 (project `agentcore-e2e`); `setup.ps1` prepares Postgres, llm-gateway, migrations, registry import and test credentials (`-ResetDb` recreates the databases because versions are immutable by hash); `serve.ps1` starts the server; `check.py` is an HTTP-only platform and conversation check that uses credentials signed with the repo's TEST identity keys (`testing.demo_identities`). The setup script generates demo keys and asks the user to add the OpenRouter and JEV keys. Not executed here.

### 7.6 Deployment (reference only)

- Image is deployed by immutable digest, never by tag (Dockerfile header). Processes to schedule separately (`docs/serve-env.md` §7): `agentcore migrate` (owner role; always re-applies; includes `--app-role` grants), `agentcore sweep --once` every few minutes, `agentcore relay` as a service. `serve` itself runs no background tasks apart from the startup migration (guarded by an advisory lock; 6 simultaneous instances applied it once, per `serve-readiness.md`).
- Scale-out (ADR 0023): stateless `serve` scales horizontally; set `AGENTCORE_DB_POOL_MAX` and size Postgres connections >= 2 pools x pool max per task (or use RDS Proxy); S3 blobs and SNS relay are opt-in.
- Infra (`C:\Users\JUAN\Documents\factored\infra`, reference only): Terraform modules include `workload`, `database`, `rds_proxy`, `ecr`, `secrets`, `scheduled_task`, `observability`, etc. In `terraform/modules/workload` the only guard found concerns `AGENTCORE_ALLOW_DEMO`; `serve-readiness.md` points out that infra should also forbid `AGENTCORE_ALLOW_DOUBLES`. Infra ADRs mentioning agent-core (`0003-agent-core-workload`, `0005-agent-core-escalado-fase-0-1`) sit in a worktree under `infra/.claude/worktrees/agent-services/docs/adr/`; their content was not read. Infra deployment state was not verified.

---

## 8. Observability, evaluation and calibration

### 8.1 Two planes (ADR 0003)

1. **Operational traces and logs** (`agent_telemetry/`): OpenTelemetry with GenAI semantic conventions, exported by OTLP/HTTP-protobuf only when an endpoint is configured; JSON logs to stderr always (level INFO, httpx/httpcore silenced below WARNING; uvicorn access log off; for exceptions only `exc_type` is logged). `agent_telemetry` creates its own tracer provider and does not install a global one.
   - Spans: `agentcore.api.request` (one per HTTP request; extracts incoming `traceparent`), `invoke_agent` (one per turn, live, opened by M4 through the local `TurnTelemetry` port; implementation `OtelTurnTelemetry` in `composition/telemetry.py`), children derived from audit events with their `ts`/`latency_ms`: `agentcore.decide`, `agentcore.rule`, `execute_tool`; `chat {model}` from the gateway; `agentcore.transfer` (validation of an agent transfer; the destination `invoke_agent` carries a span link to it).
   - Attributes pass through a closed list `ALLOWED_ATTRIBUTES` (ids, enums, counters). Content attributes only with `AGENTCORE_TRACE_CONTENT=1`; `langfuse.*` only with `AGENTCORE_TRACE_LANGFUSE=1`. Never emitted: customer text, tool args/results, prompts/responses (by default).
   - Telemetry is best-effort and deterministic-safe: it only reads events already built; replay uses the no-op implementation; recording the six paths with real telemetry gives the same bytes as the fixtures (T-M11-15 per ADR 0003).
   - The `trace_id` in every API response and problem+json is the request's trace id; without OTLP endpoint a server-generated id still correlates logs.
   - Local backend suggestion in README: Arize Phoenix (`docker run -p 6006:6006 arizephoenix/phoenix`); Langfuse works by pointing the OTLP endpoint at it (ADR 0003 amendment). The docs record that exporting from the image to Langfuse had not been tested.
2. **Audit log** (M11): per-run hash-chained events persisted in Postgres (`audit_events.sql`), with PII only in `audit` view and keyed fingerprints; transcript store separate; `GET /v1/runs/{id}/transcript` renders for the reader.

### 8.2 Health endpoints

`GET /healthz` (liveness; no dependencies), `GET /readyz` (200 `ready` / 503 `unavailable`; checks `postgres`, `keys`, `schema`, `llm_gateway`, `tool_service`; `failed`, `degraded` lists), `GET /version` (`{package, contract, sha}`). All outside `/v1`, no credential, no error details. `/readyz` probe timeout 0.8 s per dependency. Postgres down at start: `serve` still starts (`/healthz` 200, `/readyz` 503 until it returns) per the readiness inventory.

### 8.3 Evaluation

- Gate on publish: see §3.6 (`eval_suite`, guardrail/gate/monitor metrics, double yardstick). The metric catalogue lives in `agent_core/domain/metric_catalog.py`; evaluation in `agent_core/registry/evaluation/`.
- Reported results (from `docs/serve-readiness.md`, 2026-10-05, run against a reference compose; not re-run): native evaluation of the base suites (104 cases): `recepcion` 28/28 pass; `disputas` and `consultas` `gate_failed`; copilot `failed_infra`. Causes listed: noisy LLM-dependent scenarios (identical base and candidate disagree), copilot flash model omitting the `kind` envelope and pro model rejected by M8, `consultas` negative scenarios expecting `abstained`.
- Judges (`judge` metrics) are **not implemented**: `ScenarioEvaluator` is built without `judge` (`composition/serve_registry.py`), reports show `judge_notes: []`.
- Replay (`agentcore replay`): `fixture` mode runs in CI over the six recorded paths; `audit` mode against real runs **diverges** (see §9).

### 8.4 Calibration of Understand

See §3.3. Reproduce offline: `uv run python -m testing.calibration.calibrate_understand_turno --raw testing/calibration/recorded/understand_turno_raw.jsonl --out-dir testing/calibration/recorded`; `collect_jev.py` re-records JEV outputs (needs `AGENTCORE_JEV_API_KEY`); a test (`tests/m05/test_understand_turno_calibration.py`) checks the versioned artifact is exactly derivable from the recording. The calibration used in the live runbook stack is synthetic and the engine decided with it (runbook §5: `command` p_cal 0.51 to 0.57 observed).

---

## 9. Known limits and pending items

From `docs/serve-readiness.md` §4 (2026-10-05, base `356c837`, older than the pinned commit; items may have changed since, so treat as **[not re-verified at 437824f]**):

- Replay in `audit` mode of real runs diverges (one instant per turn vs several clock reads, `tool_called.result_fp` re-projected, `response_emitted` without a draft with citations); `replay <run_id>` and sessions with transfers are unsupported; `audit/replay/` unchanged since 2026-10-02.
- Judges not implemented.
- With the database down, endpoints return 500 `internal_error` (`PoolTimeout`) instead of 503.
- `/readyz` takes ~1.2 s when a dependency does not answer (1 s deadline).
- `match-cargo` only sees the last 50 transactions (tool-service maximum); the slot is the full phrase.
- Real calibration and classifier artifacts, real field grants and the real platform for grants are blocked on other teams; local runs use synthetic artifacts and a platform double (`grants-stub`) unless pointed at the real platform (the runbook did).
- Infra must forbid `AGENTCORE_ALLOW_DOUBLES` and schedule `sweep` and `relay`.
- Copilot (`copiloto-asesor` and suggestions) quality with real models is unproven: `invalid_output` with the flash model, M8 rejection with the pro model; the suggestion agent, prompt, escalation policy and eval suite are provisional (ADR 0026).
- Open from the e2e runbook: the `consulta-pqr` / `obtener_pqr(radicado)` problem is fixed on `main` (the flow now uses `leer_pqr_cliente`, `match-radicado` and `seleccionar_caso`; `docs/serve-readiness.md` still lists it as open because it predates the fix). Amounts in free text tokenised by the PII detector are addressed by ADR 0027 (`compare_on: full`), on `main`. Still open per the runbook (not re-tested): the dispute flow asking twice after "No encontré ese cargo"; the platform stage gate blocking the copilot (platform side).
- Transfer demo through `serve`: the README says the real provider for specialist choice was still open (classifier artifact synthetic) and specialists' flows did not read the transferred `problema` slot. The README also notes the one-open-run-per-session Postgres path was unverified when written.
- README "Estado" is stale: it says real adapters for tools/authz/transcript do not exist, but they exist on `main` (`adapters/tools`, `policy_authz`, `postgres_transcript`).
- Knowledge (M12): `navigate` mode is out of scope; the runtime returns `not_found` for it.
- `AGENTCORE_ALLOW_DEMO` is deprecated in favour of `AGENTCORE_ALLOW_DOUBLES`.
- Registry marking of decision models with `compare_on: full` for human review is pending (runbook §10).
- The adapter for the "agent" node needs `structured: prompted` model profiles.
- Docs index for further reading: `docs/specs/motor/00-indice.md` (start here), `docs/adr/`, `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md`, `docs/golive-checklist.md`, `docs/runbook-e2e.md`, `docs/plan-e2e-produccion.md`, `docs/tool-grants.md`.

---

## 10. Not verified in this pass (summary)

Resolved in the second pass (checked in code at `eb33f5c`): compose files and `.env.example` (variable names only), `scripts/e2e` headers, presence on `main` of `seleccionar_caso`, `consulta-pqr` with `leer_pqr_cliente` and `match-radicado`, `compare_on: full`, the widened synthetic classifier, and parity of the tool contract snapshot with tool-service.

Still not verified:
- No code was executed: tests, linters, containers, replay and calibration figures are quoted from repo docs.
- `docs/golive-checklist.md` (only its header), `docs/runbook-e2e.md`, `docs/plan-e2e-produccion.md` and the infra ADRs were not read in full.
- Platform-side behaviour (support-platform), tool-service and llm-gateway internals are described only from `agent-core`'s side.
- Real Postgres behaviour (one open run per session, advisory locks) and the S3/SNS paths were not exercised.

---

## 11. Registry (in depth)

Sources: `agent_core/registry/` (service, candidate, validation, roles, yaml_io, postgres, evaluation), `agent_core/flows/` (loader, validators, `pin_release`), `docs/specs/2026-09-29-registry-design.md` (rev. 2), ADRs 0017, 0018, 0019, 0020, 0022, 0023, and the tool-service `registry/` directory. Where the spec and the code differ, the code is stated and the difference noted.

### 11.1 What the registry is

The registry is the module `agent_core.registry`: the **only source of truth for agent definitions**. It stores *entities* (versioned data documents), groups exact versions into immutable *releases*, and controls how changes reach production through *proposals* and an *evaluation gate*. Postgres holds everything (ADR 0017); Git is not involved, and YAML is only an import/export format. The engine reads only published releases through the `RegistryPort`. A run is pinned to one release id when it starts (`RunState.release`), so a run never sees a half-published change, and the audit trail stores just the `release_id`, which the platform expands into versions, docs and diffs ("lineage").

Entity kinds (`EntityKind` in M0 plus the registry-only `eval_suite`; folder names from `FOLDERS` in `registry/yaml_io.py` and `DIRS` in `flows/registry.py`):

| Kind | Folder | What it is |
|---|---|---|
| `agent` | `agents/` | Agent definition: mode, `entry_flow`, `invocable_by`, `tools_allowed`, `budgets`, `templates`, `understand` model, `metrics` (ADR 0020), routing card |
| `flow` | `flows/` | Node graph (collect, decide, rule, tool, write tool, confirm, respond, knowledge, agent, suggest, transfer, escalate, ...) |
| `decision_model` | `decision_models/` | Typed decision model: `output_schema`, `calibrated_fields`, `providers` (`jev`, `classifier`, `rule`, `llm_structured`), `thresholds_from` |
| `policy` | `policies/` | Protected business rule (`owner`, `expr`, `rationale`, optional `locked` guardrail) |
| `template` | `templates/` | ES/PT fixed texts |
| `prompt` | `prompts/` | Prompts for generated text or the `agent`/`suggest` nodes, bound to a model profile |
| `tool` | `tools/` | `ToolDef`: `risk_class`, `min_auth_level`, `idempotent`, `readback_by`, `source`, `description`, `args_schema` |
| `model_profile` | `model_profiles/` | Model alias, structured-output mode, timeout, price |
| `language_detection`, `injection_ruleset` | `language_detection/`, `injection_rulesets/` | Guard configuration |
| `knowledge_snapshot` | `knowledge_snapshots/` | Immutable manifest of knowledge pages (seeded by import; proposals may not change it) |
| `eval_suite` | `eval_suites/` | Versioned scenario suite bound to an agent (registry-only kind; not part of a `Release`) |

A **release** (`Release` in M0) is an immutable set of exact entity versions plus release-level settings: `interrupts`, `language_detection`, `injection_ruleset`, `knowledge_snapshot`, `max_input_chars`. Releases are per agent (`release_id = "rel-" + first 16 chars of the candidate hash`; imported seed releases keep the declared id). Aliases `staging` and `prod` point to a release per agent.

### 11.2 Format on disk (authoring / import / export)

A registry directory ("authoring registry") has one folder per kind and one file per version, named `<id>@<version>.yaml`; ids may contain `/` and become subfolders (for example `templates/t/aclarar@1.0.0.yaml`, `tools/directory/list@1.0.0.yaml`, `prompts/p/copiloto@1.0.0.yaml`). Release declarations live in `releases/<id>.yaml`.

Loader rules (`flows/yaml_loader.py`, `flows/registry.py:load_registry`): safe YAML 1.2 subset (booleans only `true`/`false`, no dates, decimals as `Decimal`, no anchors, aliases, tags or duplicate keys, single document), file at most 1 MiB, depth at most 64, at most 100,000 nodes; at most 5,000 YAML files and 20,000 directory entries in total, tree depth at most 8, no symlinks or reparse points, no reserved Windows names. Invalid files become violations (`G0-01`), never abort the load.

Real examples at the pinned commit:

- Tool (`tool-service/registry/tools/radicar_pqr@1.0.0.yaml`, identical copy in `agent-core/tests/fixtures/tool-service-contract/tools/`): `id`, `version`, `risk_class: write_reversible`, `min_auth_level: step_up`, `idempotent: true`, `readback_by: idempotency_key`, `source: customer_cases`, `description`, `args_schema` (object, `additionalProperties: false`, properties `transaction_id` and `descripcion`, both required).
- Agent (`tests/fixtures/registry-e2e/agents/recepcion@1.0.0.yaml`): `id`, `version`, `mode: conversational`, `entry_flow: recepcion@1`, `invocable_by: [customer]`, `min_auth_level: session`, `subject_kinds`, `supported_locales: [es, pt]`, `default_locale`, `tools_allowed: [directory/list@1]`, `budgets{...}`, `templates{clarify, abstain, handoff, pending_ack, pending_offer, unsupported_language, input_too_large}`, `understand: understand-turno@1`, `max_clarifications`, `on_clarify_exhausted`, `default_target_queue`.
- Release declaration (`releases/recepcion-demo.yaml`): `id`, `agents: [{agent: "recepcion@^1", aliases: [prod]}]`, `flows: ["recepcion@^1"]`, `interrupts: [{id: fraude, priority: 100, action: {type: escalate, target_queue: fraude, priority: critical}}]`, `language_detection: "lang-es-pt@1"`, `injection_ruleset: "injection-rules@1"`.
- Eval suite (`eval_suites/recepcion-suite@1.0.0.yaml`): `id`, `version`, `agent_id`, `repetitions`, `thresholds`, `scenarios[]` with `principal`, `steps` (`start`, `turn`, `confirm`), `seed.tools`, `sensitive_values`, `expect`, `assertions`.

**Version references.** In authoring files a reference is `id@version` and may be a range (`^1`, `~1.2`, or major-only like `@1` in node configs). Publishing resolves them: `pin_release` (`flows/pin.py`) rewrites every reference in the closure to an **exact** version, deterministically, and refuses to pin a closure with static-validation violations (never a partial or unvalidated release). The engine never resolves ranges (`require_exact_refs` is asserted on read).

**Tool schemas.** `args_schema` and agent output schemas use a closed JSON Schema subset (`domain/schema.py`: `type`, `enum`, `properties`, `required`, `additionalProperties` as boolean, `items`; annotations `description`, `title`, `default`, `examples` are ignored; any other keyword fails closed). The tool-service's `scripts/export_registry.py` strips everything outside this subset when it generates its YAML, and enforces the stricter limits (ranges, lengths, date format) itself at run time.

Export / import round trip: `agentcore registry export <release_id> <dir>` writes YAML (`dump_entities`, `dump_release`); `import` after `export` keeps `content_hash` identical (spec 5.6).

### 11.3 Storage and schema (Postgres)

Defined in `agent_core/registry/postgres/schema.sql` (applied by `agentcore migrate`). Tables carry the `reg_` prefix and live in the connection's `search_path`.

| Table | Mutability | Content |
|---|---|---|
| `reg_blobs` | insert-only | `hash` (sha256 of the canonical bytes), `bytes`. Optionally replaced by S3 (`AGENTCORE_BLOB_BUCKET`; `migrate` then drops the FK to `reg_blobs`, integrity is checked on every read) |
| `reg_entity_versions` | insert-only | `(kind, id, version)` to `content_hash`, `docs` (description, rationale, changelog), proposal, author, time |
| `reg_releases`, `reg_release_entities`, `reg_release_eval_suites` | insert-only | Release, its exact entity set, and the suites with which it passed the gate |
| `reg_approvals`, `reg_eval_runs` | insert-only | Human decisions (with `yardstick_loosened`) and evaluation reports with verdict (`pass`, `fail`, `failed_infra`) |
| `reg_events`, `reg_alias_log` | insert-only (sequence-numbered) | Registry audit events and every alias change with actor and reason |
| `reg_release_status` | controlled update | `active` or `revoked` (separate so `reg_releases` is strictly immutable) |
| `reg_aliases` | controlled update | `(agent_id, alias)` to `release_id`; changes only through publish (`staging`) and promote |
| `reg_agent_pause` | controlled update | Pause/resume state per agent; a paused agent leaves the `recepcion` directory |
| `reg_proposals`, `reg_proposal_changes` | mutable while `draft` | Proposal JSON and the working draft |
| `reg_publish_keys`, `reg_draft_writes` | insert-only | Publish idempotency keys and idempotent draft-write records |

Immutability is enforced by triggers (`*_no_update`, `*_no_truncate`) in addition to grants. `content_hash = sha256(canonical_bytes(entity))` (JCS). `release_hash` = hash of the release without id/status; `candidate_hash` = hash of `{release_hash, sorted (kind, id, version, content_hash)}`. Names differ slightly from the spec text (the spec calls the audit table `registry_events`; the code creates `reg_events`).

Entity content is validated by Pydantic on write and decoded with a hash check on read; a mismatch raises `IntegrityError` and the engine escalates instead of serving it. Contract schemas for the registry API are generated in `contracts/registry/` and `contracts/registry-openapi.json`.

### 11.4 Lifecycle: proposals and the gate

Every change is a **proposal** (same cycle for a person, the builder agent or the automatic detector; `origin` is `manual`, `builder_chat`, `auto_detect` or `import`).

```mermaid
stateDiagram-v2
    [*] --> draft: create_proposal (base = staging release of the agent)
    draft --> draft: put_draft (expected_rev)
    draft --> candidate: freeze (build candidate + validate)
    candidate --> evaluated: evaluate = pass
    candidate --> draft: evaluate = fail (gate_failed)
    candidate --> candidate: evaluate = failed_infra
    evaluated --> approved: approve (human, step-up, candidate_hash)
    evaluated --> draft: reject (reason kept)
    candidate --> draft: reopen
    evaluated --> draft: reopen
    approved --> draft: reopen
    approved --> published: publish (idempotency key)
    approved --> draft: base moved (proposal_stale)
    published --> [*]
```

`ProposalState` in code is exactly `draft, candidate, evaluated, approved, published`. Any other transition raises `illegal_transition` (409).

```mermaid
flowchart TD
    A[Builder, person or detector<br/>creates proposal] --> B[put_draft: changes<br/>EntityDraft kind + content + docs]
    B --> C{freeze}
    C -->|violations| B
    C -->|ok| D[Candidate: authoring registry in memory<br/>= base release entities + changes<br/>pin_release -> exact versions<br/>candidate_hash]
    D --> E[evaluate with eval_suite<br/>ScenarioEvaluator in sandbox]
    E -->|fail| B
    E -->|failed_infra| E
    E -->|pass| F[Human approver<br/>approve candidate_hash<br/>step-up required]
    F -->|reject| B
    F --> G[publish: one transaction<br/>lock agent, check staging == base,<br/>rebuild + same hash, insert blobs/versions/release,<br/>move staging, audit]
    G --> H[staging alias]
    H --> I[promote to prod<br/>human + step-up]
    I --> J[Engine resolves prod<br/>new runs pin the new release]
    G -.->|base moved| B
```

What `freeze` and `validate` check (`registry/validation.py`, `registry/candidate.py`, M1 rules):

- **Static validation (M1, `agent_core/flows/`)**: the G0 rules over every flow in the closure, per-agent checks (AG-xx), metric rules (MT-01 to MT-06) and release checks. Selected G0 rules: G0-01 schema, G0-02 dangling reference, G0-03/04 graph structure and wait-free cycles, G0-05 write invariant (every write behind `confirm` unless `write_draft`), G0-06 failure branches with a safe exit, G0-07/22 `agent` node limited to read/compute tools and its output barred from writes/rules/verify, G0-08 no business literals in `rule` expressions, G0-10 namespace reads, G0-11 `decide` branches only on calibrated fields, G0-12 template/prompt for every agent locale, G0-14 outcome/mode compatibility, G0-15 prompt needs a model profile, G0-16 task flows have no waiting nodes, G0-17..21 knowledge, G0-23/24/25 draft writes and `agent`/`suggest` documentation and prompted profiles, G0-26/27 transfer, G0-28 `suggest` node rules (ADR 0026), G0-29 `capture_start`. Run offline with `agentcore validate <dir> [--json]`.
- **Registry rules (`REG-*`)**: `REG-SCHEMA` (draft not parseable as its kind), `REG-DUPLICATE`, `REG-KIND`, `REG-AGENT`, `REG-PIN`, `REG-PROPOSAL`, `REG-UNREFERENCED`, `REG-VERSION` (new version must be greater than the base version) and `REG-VERSION-TAKEN` (that `(kind, id, version)` already exists), `REG-LIMIT` (at most 50 changes, 262,144 bytes per entity, 200 flow nodes by default), `REG-KNOWLEDGE` (proposals cannot create or change knowledge snapshots), `REG-LOCKED` (locked policies and interrupts cannot be removed, unlocked or changed; only an admin sets `locked`), `REG-SUITE` (suite problems: format, thresholds, scenario limits, disabled `dataset` source).
- **Cascade versioning**: if a changed entity is referenced by exact version from another base entity, the referrer gets an automatic patch bump (repeated to a fixed point) and generated docs; `freeze` returns them in `Candidate.auto_bumped`.
- **Release settings draft**: release-level fields (interrupts, language detection, injection ruleset, `max_input_chars` up to 100,000) change through a draft of kind `release_settings` (no `id`/`version`); replacing interrupts is treated as a sensitive change.

**Evaluation gate** (ADR 0018 amended by ADR 0020; `registry/evaluation/`): `evaluate` is synchronous and runs up to three measurements (new yardstick on the candidate; old yardstick on base and on candidate), each scenario `repetitions` times in a per-run sandbox (`LocalSandbox`: in-memory tools seeded by `seed.tools`; the evaluator refuses a `ToolExecutor` without `is_sandbox = True`). Scoring is computed from engine events. Platform guardrails count 0 tolerance (`platform_pii_leak`, `platform_unverified_success_claim`, `platform_unverified_write`, `platform_unapproved_knowledge_citation`); each agent `gate` metric must not be worse than the base beyond its `noise_margin` and must clear its `floor` if new or changed; no composite score. Infrastructure failure of the gateway, a decision provider or the sandbox makes the whole evaluation `failed_infra` (never a partial pass). A `pass` is bound to the `candidate_hash`; any edit invalidates it. If the proposal loosens the yardstick (deleting or changing existing metrics, scenarios, thresholds), the report carries `yardstick_changes` and `approve` needs `accept_yardstick_loosened=true`, otherwise `loosening_not_accepted`. No manual override of a failed gate exists. The optional `Judge` hook only adds notes; it is not implemented in `serve` (section 9).

**Roles and who may do what** (`registry/roles.py`, enforced server side, not by prompt): builder principals operate the registry (`type=builder`); `constructor` may create, draft, validate, freeze, reopen, evaluate and read; approve, reject, publish, promote, revoke, pause/resume and seed import need a **human** (`attrs.actor = "human"`) with role `aprobador` (or `admin`) and `auth.level = step_up`; `exporter` reads the export API; the builder agent's service credential cannot carry `actor=human`. Autonomous proposals (`origin=auto_detect`) are rate-limited (10 proposals per rolling 24 h, 20 evaluations per proposal; `registry/quotas.py`). Errors are `RegistryErrorCode`: `validation_failed` 422, `gate_failed` 409, `proposal_stale` 409, `candidate_changed` 409, `illegal_transition` 409, `forbidden_role` 403, `step_up_required` 403, `integrity_error` 500, `not_found` 404, `loosening_not_accepted` 409, `idempotency_conflict` 409, `quota_exceeded` 429.

**Publish and promotion** (spec 5.4 and code in `registry/service.py`): in one transaction, take an advisory lock on the agent, check `staging` still equals the proposal base (else `proposal_stale` and back to `draft`), rebuild the candidate and compare hashes (`candidate_changed`), require an approval for that hash, insert blobs, versions, release, entities, suite refs and `release_status=active`, move `staging`, write `reg_alias_log` and `reg_events`. An `Idempotency-Key` makes retries return the same release. `promote(agent, alias, release)` points an alias at an active release of that agent (rollback is promoting an older release; no new gate). `revoke(release, reason)` is immediate and ungated; a release that `prod` points to cannot be revoked first; open runs pinned to a revoked release escalate with `release_revoked`. Publish, promote and revoke feed the outbound events `release.published`, `release.promoted`, `release.revoked` through the outbox.

### 11.5 Runtime read path: how agent-core consumes the registry

```mermaid
sequenceDiagram
    autonumber
    participant API as POST /v1/runs
    participant T as TurnEngine
    participant R as PostgresRegistry (RegistryPort)
    participant S as Postgres reg_* tables
    participant B as BlobStore (Postgres or S3)
    API->>T: start_run(agent selector, alias prod or pinned version)
    T->>R: resolve_release(selector, principal)
    R->>S: resolve alias to release_id (or latest release for an agent version)
    R->>S: release_status (cached, short TTL) must be active
    R->>S: release + release_entities (cached by release_id)
    R-->>T: Release with exact refs, run pinned to release_id
    loop each node / model / tool
        T->>R: get(EntityRef, kind)
        R->>S: version row
        R->>B: blob by content_hash (hash verified)
        R-->>T: decoded entity
    end
```

`PostgresRegistry` (`registry/postgres/runtime.py`): `resolve_release` follows `prod` by default (or `staging` when the selector asks, or an exact agent version if pinned); a revoked release raises `KeyError` so the run cannot start; releases are cached by id (immutable) and status is re-read after a short TTL (5 s default) so a revocation takes effect quickly; `get` verifies the content hash. `PostgresRegistry.release` is also given to the engine as `EngineDeps.releases`. The agent directory used by `recepcion` transfers (`directory/list`) is `RegistryDirectory`: agents whose `prod` alias points to an active release and whose `Agent.routing` card carries the requested tag; pausing an agent removes it from the directory without changing `prod`. Evaluation uses `SnapshotRegistry`, an in-memory read-only `RegistryPort` built from a candidate (or the published base release), so unpublished versions never enter the published tables.

**Tools: registry versus tool-service.** The registry owns the `ToolDef` (risk class, auth level, idempotency, read-back, source, args schema); the tool-service implements the data side by tool name. They must agree, so:

- tool-service generates `registry/tools/<id>@<version>.yaml` from its own catalog (`uv run python scripts/export_registry.py`, `--check` fails on drift); at `origin/main` it ships 7 tools: `buscar_transacciones`, `leer_movimientos`, `leer_perfil`, `leer_pqr_cliente`, `leer_productos`, `obtener_pqr`, `radicar_pqr`.
- agent-core keeps a snapshot copy in `tests/fixtures/tool-service-contract/` (refreshed with `uv run python scripts/sync_tool_contract.py [--check] [path]`) and the test `tests/registry/test_tool_contract_drift.py` compares `id`, `version`, `risk_class`, `min_auth_level`, `idempotent`, `readback_by`, `source` and `args_schema` of the fixtures in `tests/fixtures/registry-e2e/tools/` with it (descriptions may differ). I diffed the snapshot against tool-service `origin/main` and all 7 files are identical.
- `registry-e2e` imports 6 of the 7 tool-service tools (not `leer_perfil`) plus engine-served tools (`seleccionar`, `seleccionar_caso`, `convertir_moneda`, `obtener_handoff`, `leer_transcript`), directory tools (`directory/list`) and the builder's registry tools (`registry/create_proposal`, `put_draft`, `validate`, `freeze`, `evaluate`, `reopen`, `get_proposal`, `get_entity`, `get_write`, `list_versions`). Each agent lists the tools it may use in `tools_allowed` (M1 checks this against the flows at freeze time); an unknown tool name at the service answers `404 unknown_tool`. Whether the runtime additionally rejects a tool outside `tools_allowed` was not traced in code.
- A change to a tool (new field, different risk class) is made in tool-service first, regenerated there, then synced into agent-core fixtures and into the registry as a new tool version through a proposal. The shared provider contract has its own version (`contracts/tool-provider-version.txt`; minor for optional additions, major otherwise).

**The builder** (ADR 0019) uses `BuilderToolExecutor` (`composition/builder_tools.py`) over `RegistryService` with its own service credential (role `constructor`, never human); there are no approve/publish/promote/revoke tools. `write_draft` tools are the only writes without `confirm`; they stay idempotent and read-back verified. The registry API and CLI exercise the same service.

### 11.6 Access: API, CLI, export

| Surface | Endpoints / commands | Credential |
|---|---|---|
| HTTP `/v1/registry` (only with `--registry-api`) | `POST/GET /proposals`, `GET /proposals/{pid}`, `PUT /proposals/{pid}/draft`, `POST /proposals/{pid}/{validate,freeze,reopen,evaluate,approve,reject,publish}`, `POST/GET /aliases/{agent}/{alias}`, `POST/GET /agents/{agent}/pause`, `POST /agents/{agent}/resume`, `GET /versions/{kind}/{id}`, `GET /entities/{kind}/{id}`, `GET /releases/{rid}`, `GET /releases/{a}/diff/{b}`, `POST /releases/{rid}/revoke`, `GET /runs/{run_id}/lineage` | staff-issued JWS (keys from `--staff-keys`) |
| HTTP `/v1/export` | `GET /runs`, `GET /runs/{id}/events`, `GET /registry-events` (paginated, `limit` max 500) | staff credential with `exporter` role |
| CLI `agentcore registry` | `import <dir>`, `export <release> <dir>`, `propose`, `draft`, `validate`, `freeze`, `reopen`, `show`, `evaluate`, `approve`, `reject`, `publish`, `promote`, `revoke`, `diff`, `lineage` | `--credential`/`AGENTCORE_CREDENTIAL` verified by `--verifier` (real: `agent_core.composition.registry:staff_verifier`); `evaluate` also needs `--harness`; the demo doubles need `AGENTCORE_ALLOW_DEMO=1` |
| CLI `agentcore validate <dir>` | static validation without a database | none |

### 11.7 How to add or change an entry

**Seed a new agent (first release; needs an admin human with step-up, only if the agent has no release):**

1. Create the files in a registry directory: the agent, its flows, decision models, policies, templates, prompts, tools, model profiles, `language_detection`, `injection_ruleset`, optional `knowledge_snapshot` and `eval_suite`, and a `releases/<id>.yaml` declaration naming the agent and its aliases.
2. Validate offline: `uv run agentcore validate <dir>` (add `--json` for stable output). CI runs the same on the demo registries.
3. Make sure every tool matches the tool-service catalog (regenerate there; run `tests/registry/test_tool_contract_drift.py`).
4. Import: `agentcore registry --verifier agent_core.composition.registry:staff_verifier import <dir>` with `AGENTCORE_REGISTRY_DSN` and a short-lived credential. Import pins the release, inserts it as published (`origin = import`) and points `staging` and `prod`; it fails if the agent already has a release, rejects a suite with problems, and takes the same per-agent lock as publish. Versions are immutable by hash: re-importing different content under an existing `id@version` fails with "ya existe con otro contenido", which is why a changed fixture needs a new version or a recreated database in local setups.

**Change an existing agent (the normal path):**

1. `propose` (or `POST /v1/registry/proposals`) naming the agent; the base is its `staging` release.
2. `draft`: send changes as `EntityDraft` (`kind`, `content` including `id` and a **higher** `version`, `docs{description, rationale, changelog}`), with the current `rev`. Use `kind: release_settings` for release-level fields. Add or change the `eval_suite` in the same proposal if the yardstick changes.
3. `validate` while iterating; `freeze` to build the candidate (violations return plain-language messages with rule, entity and path).
4. `evaluate` with the suite id; read the report (per scenario results, guardrails, `yardstick_changes`). A `fail` returns the proposal to `draft`.
5. A human approver `approve`s the exact `candidate_hash` (accepting yardstick loosening explicitly if present), then `publish` with an idempotency key (this moves `staging`), then `promote` to `prod`.
6. Verify: `GET /v1/registry/runs/{run_id}/lineage` for a new run shows the release and the changed versions; `diff` compares two releases; `revoke` or promoting the previous release rolls back.

Do not edit a published version in place: a published `(kind, id, version)` is immutable and its content hash is checked on every read. Never put real customer data in an entity or a suite (CLAUDE.md rule 5); suites use synthetic principals and `scripted` seeds.

### 11.8 Registry limits and open points

- The spec rev. 2 notes that rejecting an agent without a suite at validation time is not wired (evaluation spec 13.14); an agent without a suite simply cannot be evaluated through the gate.
- States `abandoned` and `stale` as stored states, automatic approval, per-entity autonomy settings and the `dataset` scenario source are not implemented (phase 2 in the spec).
- Judges are not implemented in `serve` (`composition/serve_registry.py`); `judge_notes` stays empty.
- Tools in evaluation are in-memory doubles by design; drift against the real tool-service is covered only by the contract drift test.
- Evaluation of LLM-dependent scenarios is noisy (identical base and candidate can disagree; `docs/serve-readiness.md`), so margins and repetitions matter.
- The marking of decision models with `compare_on: full` for mandatory human review is pending (runbook section 10).
- Whether the Postgres registry path and the S3 blob path behave as described was not exercised here; they are covered by integration tests (`tests/integration/test_registry_postgres.py`, `tests/registry`) that were not run.
