# HOS Alert Regulation Audit

**Date:** 2026-08-03  
**Rule pack under audit:** `fmcsa-us-property@1.3.0`  
**Spec source:** [HOS-Regulations.pdf](./HOS-Regulations.pdf) — *Hours-of-Service Regulatory Specification for the Rules Engine*  
**Scope:** Audit documentation only (no engine code changes).  
**Audience:** Engine / compliance developers.

---

## 1. Executive summary

The live engine emits **9 distinct alerts**: five `ViolationType` values × `WARNING`/`VIOLATION` for four clocks, plus `RESTART_INVALID` at `VIOLATION` only. `CRITICAL` exists in enums, notifier stages, and UI labels but is **never emitted** by calculators.

Against PDF **Ruleset A** (interstate property-carrying, 49 CFR §395.3), posture is **partial / high regulatory risk**:

| Area | Posture |
|------|---------|
| Alert surface for core clocks | Present (11h / 14h / break / weekly / restart-related) |
| Clock math vs PDF §3.2–3.5 | Multiple high-confidence divergences (14h shape, break reset, YM-as-driving, obsolete 1–5 AM restart gate, incomplete split sleeper) |
| Severity model vs PDF §8.3 | Mismatch (`WARNING`@30m / weekly@2h; no ADVISORY@60m; no overage CRITICAL) |
| Rulesets B/C/D, form & manner, exceptions | Not implemented |
| Live vs backtest evaluation | Sweeper undercounts open-segment time (no `as_of` truncation) |

The “~17 alerts” figure from design docs is **not** a second live catalog — it matches staged thresholds (`WARNING_30M` / `WARNING_15M` / `VIOLATION` × rule keys) and/or PDF findings that are not wired yet.

**Resolved (2026-08-03 / `@2.5.0`):** Product dropped the obsolete 1–5 AM gate. ≥34h OFF/SB resets the weekly cycle; `RESTART_INVALID` is no longer emitted.

---

## 2. Alert inventory matrix

Emit path for all alerts: `RulePack.evaluate()` → calculators → `ComplianceResult.violations` → sweeper publishes to Redis `compliance_alerts`.

| # | Type | Severity | `rule_ref` | PDF | CFR | Warning threshold | Emit site |
|---|------|----------|------------|-----|-----|-------------------|-----------|
| 1 | `DRIVING_LIMIT` | `WARNING` | `§ 395.3(a)(3)(i)` | §3.2 row 1 | 395.3(a)(3)(i) | ≤30 min remaining | `calculators.py` `check_driving_limit` |
| 2 | `DRIVING_LIMIT` | `VIOLATION` | `§ 395.3(a)(3)(i)` | §3.2 row 1 | 395.3(a)(3)(i) | driven ≥ 11h | same |
| 3 | `DUTY_WINDOW` | `WARNING` | `§ 395.3(a)(2)` | §3.2 row 2 | 395.3(a)(2) | ≤30 min remaining | `check_duty_window` |
| 4 | `DUTY_WINDOW` | `VIOLATION` | `§ 395.3(a)(2)` | §3.2 row 2 | 395.3(a)(2) | elapsed ≥ 14h | same |
| 5 | `REST_BREAK` | `WARNING` | `§ 395.3(a)(3)(ii)` | §3.2 row 3 | 395.3(a)(3)(ii) | ≥7.5h driving since break | `check_rest_break` |
| 6 | `REST_BREAK` | `VIOLATION` | `§ 395.3(a)(3)(ii)` | §3.2 row 3 | 395.3(a)(3)(ii) | ≥8h driving since break | same |
| 7 | `WEEKLY_CYCLE` | `WARNING` | `§ 395.3(b)` | §3.2 row 4 | 395.3(b) | ≤2h remaining | `check_weekly_cycle` |
| 8 | `WEEKLY_CYCLE` | `VIOLATION` | `§ 395.3(b)` | §3.2 row 4 | 395.3(b) | duty ≥ cycle limit | same |
| 9 | `RESTART_INVALID` | `VIOLATION` | `§ 395.3(c)` | §3.2 row 5 | 395.3(c) | *(none — VIOLATION only)* | `check_restart` |

**Not live:** `ViolationSeverity.CRITICAL` (`schemas.py`); design-doc stages `WARNING_15M` / rule keys `11H_DRIVE` etc. (`docs/launch/01-foundation-and-decisions.md` ~1454).

**Orchestration:** `rule_pack.py` lines 88–108 call all five calculators. Sweeper: `sweeper.py` lines 64–91 (evaluates **without** `as_of`).

---

## 3. Per-alert deep dives

### 3.1 `DRIVING_LIMIT` / `WARNING`

**PDF requirement (§3.2):** Sum Driving-status minutes since last valid 10-hour reset. Severity scales with overage (§8.3). Preventive tier is ADVISORY when within **60 minutes** of the 11h limit while still driving.

**How code calculates:**

1. State machine accumulates `cumulative_driving_seconds` for statuses in `_DRIVING_STATUSES` = `{DRIVING, YARD_MOVE}` (`state_machine.py` 54–58, 166–170).
2. `check_driving_limit` computes `remaining = 11h - driven`; emits `WARNING` when `0 < remaining ≤ 1800s` (`calculators.py` 33–39, 62–92).

**Verdict: FAIL (partial)**

- Threshold is 30 min, not PDF ADVISORY 60 min (§8.3).
- Yard Move incorrectly counts toward the 11h clock (see §4.3).
- Severity label is `WARNING`, not `ADVISORY`.

**Evidence:** `calculators.py:33-39,80-92`; `state_machine.py:54-58,166-170`.

---

### 3.2 `DRIVING_LIMIT` / `VIOLATION`

**PDF requirement (§3.2):** The moment cumulative driving exceeds 11:00, every additional **driving** minute is in violation. Severity scales with overage (>15 min → CRITICAL per §8.3).

**How code calculates:** Emits `VIOLATION` when `driven >= MAX_DRIVING_SECONDS` (11h), with `overage_seconds` set (`calculators.py` 66–78). No promotion to `CRITICAL` based on overage. `_severity_from_remaining` exists but is unused and never returns `CRITICAL` (`calculators.py` 42–48).

**Verdict: FAIL (partial)**

- Limit trigger at ≥11h is directionally correct for Driving.
- False positives when overage is YM-only (YM should not consume 11h).
- No CRITICAL overage tier; `CRITICAL` enum unused by calculators.

**Evidence:** `calculators.py:42-48,66-78`; `schemas.py:26-31`.

---

### 3.3 `DUTY_WINDOW` / `WARNING`

**PDF requirement (§3.2):** Wall-clock 14h from first on-duty/driving after a 10h reset. OFF/lunch/waiting do **not** pause the window (exception: qualifying sleeper split §3.4). ADVISORY within 60 min of limit while still driving.

**How code calculates:**

1. `duty_window_elapsed_seconds` increments only when `not is_rest` and `is_duty` (`state_machine.py` 161–164) — so OFF/SB/PC **pause** the window.
2. Warning at ≤30 min remaining (`calculators.py` 108–137).

**Verdict: FAIL**

- Wrong window shape (pause on rest vs wall-clock).
- Warning threshold 30m vs PDF 60m ADVISORY.
- Split-sleeper exclusion of qualifying periods not implemented (flag only).

**Evidence:** `state_machine.py:161-164`; `calculators.py:108-137`; split flag `state_machine.py:177-184`.

---

### 3.4 `DUTY_WINDOW` / `VIOLATION`

**PDF requirement (§3.2):** Any **Driving** segment that begins, continues, or ends after window start + 14:00 is a violation. On-duty not driving after hour 14 is **not** itself a violation.

**How code calculates:** Emits `VIOLATION` when `elapsed >= 14h` regardless of current duty status (`calculators.py` 112–125). Elapsed only counts non-rest duty minutes (paused by OFF/SB/PC).

**Verdict: FAIL**

1. **False negatives:** Real wall-clock overage while OFF can leave `elapsed < 14h`, so no alert until more ON/D accumulates — and the true 14h wall-clock may already have passed.
2. **False positives vs PDF:** Fires on elapsed ≥14h even if the driver is ON-DUTY not driving (PDF: only driving after hour 14 violates).
3. Split sleeper does not exclude qualifying SB/OFF periods from the 14h clock.

**Evidence:** `calculators.py:112-125`; `state_machine.py:161-164,177-184`.

---

### 3.5 `REST_BREAK` / `WARNING`

**PDF requirement (§3.2):** Reset break accumulator on any consecutive **non-driving** ≥30 min (OFF, SB, **or ON-DUTY not driving**). Warn/prevent when approaching 8h driving without that interruption. §8.3 ADVISORY within 60 min of limits generally; break specifically escalates to SERIOUS when missed.

**How code calculates:** Warns when `driving_since_break >= 8h - 30min` (`calculators.py` 171–183). Reset only when `is_rest and duration >= 1800` (`state_machine.py` 172–175) — rest = OFF/SB/PC only (`state_machine.py` 40–44).

**Verdict: FAIL (partial)**

- 30-min remaining warning is plausible for preventive UX, but reset logic is too narrow (ON-DUTY not driving ≥30 min should qualify and does not).
- YM counts as driving toward the break accumulator (`_DRIVING_STATUSES`), inflating break risk.

**Evidence:** `calculators.py:153-183`; `state_machine.py:40-44,54-58,166-175`.

---

### 3.6 `REST_BREAK` / `VIOLATION`

**PDF requirement (§3.2):** If accumulator exceeds 8:00 **and Driving status continues**, flag from that minute forward.

**How code calculates:** Emits `VIOLATION` when `driving_since_break >= 8h` with no check that the driver is currently Driving (`calculators.py` 157–169).

**Verdict: FAIL (partial)**

- Can fire while not driving (status-agnostic) once the accumulator crossed 8h.
- Misses resets that should have occurred during ON-DUTY not driving ≥30 min → **false positives**.
- Conversely, if the driver used ON-DUTY lunch as the break, code never resets → later true driving looks like a break violation.

**Evidence:** `calculators.py:157-169`; `state_machine.py:172-175`.

---

### 3.7 `WEEKLY_CYCLE` / `WARNING`

**PDF requirement (§3.2 / §8.3):** Rolling 60/7 or 70/8 of ON+D. ADVISORY when cycle above **90%** (not a fixed 2h remaining). Driving while sum ≥ cap is the violation condition.

**How code calculates:** `compute_weekly_duty_seconds` sums ON/DRIVING/YM in the rolling window, optionally cut at a valid 34h restart (`replay.py` 227–268). Warning when `hours_remaining <= 2.0` (`calculators.py` 221–233). Limit from `settings.WEEKLY_CYCLE_LIMIT_HOURS` (default 70).

**Verdict: FAIL (partial)**

- 2h remaining ≈ 97% of 70h used, not PDF 90% ADVISORY (~7h remaining on 70h).
- YM correctly counts toward cycle per PDF §3.5 (ON), but also incorrectly toward 11h (separate bug).
- Restart gate may refuse a legally valid 34h OFF+SB reset when 1–5 AM fails (see alert #9 / §4.4).

**Evidence:** `calculators.py:201-233`; `replay.py:227-268,125-137`.

---

### 3.8 `WEEKLY_CYCLE` / `VIOLATION`

**PDF requirement (§3.2):** Driving while the rolling ON+D sum ≥ cap is a violation. Off-duty/SB never count. 34h OFF+SB restart zeros the cycle; **no** 1–5 AM requirement.

**How code calculates:** `VIOLATION` when `weekly_duty_seconds >= limit_seconds` regardless of current status (`calculators.py` 206–219). Restart credit requires `is_valid_restart_period` (≥34h **and** ≥2 home-terminal 1–5 AM overlaps) (`replay.py` 125–137, 243–245).

**Verdict: FAIL (partial)**

- Duty statuses for the sum (ON/D/YM) are mostly aligned with PDF ON+D.
- Status-agnostic fire (not “while driving”) can over-alert vs PDF wording.
- **False weekly violations** when a qualifying 34h rest lacked two 1–5 AM periods — PDF forbids that gate; code withholds the reset.

**Evidence:** `calculators.py:206-219`; `replay.py:96-137,243-245`; `state_machine.py:145-155`.

---

### 3.9 `RESTART_INVALID` / `VIOLATION`

**PDF requirement (§3.2 row 5):** 34h restart is **not a violation source** — it is a reset mechanism. Detect OFF+SB ≥34:00 and zero the cycle. Explicitly: *“no 1–5 a.m. requirement (those provisions were removed years ago — **do not implement them**)”*.

**How code calculates:**

1. On leaving a ≥34h rest, `is_valid_restart_period` is checked; failure sets `invalid_restart_at_end` (`state_machine.py` 145–155).
2. `check_restart` emits `RESTART_INVALID` / `VIOLATION` when that flag is set (`calculators.py` 240–264).
3. Weekly math also refuses reset without two 1–5 AM periods (`replay.py` 125–137).

**Verdict: FAIL**

- Invents a violation type the PDF says should not exist as a finding source.
- Enforces obsolete 1–5 AM logic → **false positive** risk vs this spec (and vs current federal restart rules as stated in the PDF).
- Conflicts with `regulatory-scope.md` (which currently requires 1–5 AM) — see §6.

**Evidence:** `calculators.py:240-264`; `state_machine.py:145-155`; `replay.py:96-137`.

---

## 4. Cross-cutting calculation issues

### 4.1 14-hour window is wrong shape

| | PDF §3.2 | Code |
|--|---------|------|
| Clock type | Wall-clock from first on-duty after 10h reset | Accumulated non-rest duty seconds |
| OFF / lunch / waiting | Do **not** pause | Pause (`is_rest` skips accumulation) |
| Violation object | Driving after hour 14 | Elapsed ≥14h any status |
| Sleeper split | Qualifying periods excluded from 14h | Flag only; no exclusion |

**Evidence:** `state_machine.py:122-124,161-164`; `calculators.py:108-125`.

### 4.2 Break reset too narrow

PDF: any consecutive non-driving ≥30 min (including ON-DUTY not driving).  
Code: reset only on OFF/SB/PC with `duration >= 1800` (`state_machine.py:172-175`).

### 4.3 Yard Move misclassified as driving

PDF §3.5: YM counts as ON — consumes 14h + cycle, **not** the 11h driving clock. Flag highway-speed YM as falsification separately.  
Code: YM ∈ `_DRIVING_STATUSES` and `_DUTY_STATUSES` (`state_machine.py:46-58`) → counts toward 11h, break accumulator, duty window, and weekly cycle. Confirmed by unit test name `test_yard_move_counts_as_driving_and_weekly_duty`.

### 4.4 34h restart 1–5 AM logic obsolete / incorrect per PDF

PDF §3.2: do not implement 1–5 AM.  
Code: `count_1_to_5_am_periods` + `is_valid_restart_period` (`replay.py:96-137`); used for weekly reset and `RESTART_INVALID`.  
Also documented as required in `docs/regulatory-scope.md` item 5 — **spec conflict** (§6).

### 4.5 Split sleeper incomplete

PDF §3.4: 7+3 / 8+2 pairing (10h total), neither period against 14h, rematch 11/14 from end of **first** period, retrospective look-back.  
Code (`state_machine.py:177-184`): sets `split_sleeper_active` on SB ≥8h; may stash SB ≥2h in `pending_sb_block`; **no** pairing completion, **no** clock rematch, **no** alerts. Thresholds omit the 7h primary berth option. `regulatory-scope.md` lists split sleeper as enforced (§395.1(g)(1)) but behavior is effectively stubbed.

### 4.6 Severity model mismatch — **remediated in `fmcsa-us-property@2.1.0`**

Historical finding (pack `@1.3.0` / `@2.0.0`): WARNING@30m, weekly@2h remaining, CRITICAL never emitted.

**Adopted mapping (PDF §8.3 → existing enums; names unchanged):**

| PDF §8.3 | `ViolationSeverity` | Thresholds |
|----------|---------------------|------------|
| ADVISORY | `WARNING` | Within 60 min of 11h/14h/8h break; weekly used >90% |
| SERIOUS | `VIOLATION` | Limit reached or ≤15 min overage; missed break; cycle exceeded |
| CRITICAL | `CRITICAL` | Overage >15 min on driving / duty-window limits |

See also `docs/regulatory-scope.md` § Severity Mapping and Appendix C below.

### 4.7 Live sweeper undercount

`RulePack.evaluate(..., as_of=...)` truncates and closes the open segment via `truncate_timeline_to` (`rule_pack.py:72`, `replay.py:47-87`).  
**Sweeper** calls `evaluate` **without** `as_of` (`sweeper.py:87-91`), so the last event keeps `duration_seconds = 0` (`state_machine.py:91-93`). Live clocks can lag until the next status change. Backtest / `alert_detail` pass `as_of` and do not share this undercount.

---

## 5. PDF findings not implemented

Grouped for backlog. None of these are currently alertable in `fmcsa-us-property@1.3.0`.

### 5.1 Ruleset selection (PDF §2) — priority: high for multi-authority fleets

- Per-driver `operating_authority` (INTERSTATE / TX_INTRASTATE)
- `short_haul_eligible` daily exemption test + fallback to full ruleset
- RODS-required-for-day flag when exemption fails
- 8-in-30 ELD trigger counter

### 5.2 Ruleset B — Federal 150 air-mile (§4) — priority: high for short-haul clients

- 150 air-mile great-circle radius check
- Return + release within 14h (CDL) / 14h|16h pattern (non-CDL §4.2)
- Suppress 30-min break under short-haul
- 8-in-30 alerts: warn@5 / urgent@7 / viol@9+

### 5.3 Ruleset C — Texas intrastate (37 TAC §4.12) — priority: high for TX corridor

- 12h drive / 15h **accumulated** on-duty (not federal wall-clock)
- 70÷7 only; no 60/7
- No 30-minute break rule
- OOS-risk severity for 12/15 overages (8h OOS)
- Texas sleeper split rules (§5.3)

### 5.4 Ruleset D — Texas 150-mi exemption (§6) — priority: medium

- 150 air-mile + return/release within **12h** + 8h off between tours
- Parallel Ruleset C accumulators; RODS relief only

### 5.5 Exceptions (PDF §3.5) — priority: medium (false-positive suppression)

| Exception | Status |
|-----------|--------|
| Adverse driving annotation (§395.1(b)) → 13h/16h day | Not implemented |
| 16h short-haul (§395.1(o)) | Not implemented |
| PC as OFF | **Implemented** (PC ∈ `_REST_STATUSES`) |
| PC abuse heuristics (>3h, toward load, after hours exhaust) | Not implemented |
| YM as ON not Driving | **Not** — currently Driving |
| YM highway-speed / public-road falsification flags | Not implemented |

### 5.6 Form & manner (PDF §3.6) — priority: medium (SMS BASIC parity)

- Missing daily certification
- Missing required fields
- Unassigned driving
- Log edits (especially D→OFF/PC/YM)
- ELD malfunction >8 days on paper

### 5.7 Design-doc stages not shipped — priority: low/product

From `docs/launch/01-foundation-and-decisions.md` ~1454:

- Rule keys: `11H_DRIVE`, `14H_DUTY`, `30M_REST`, `60_70H_CYCLE`
- Stages: `WARNING_30M`, `WARNING_15M`, `VIOLATION`

Live code uses `ViolationType` + `WARNING`/`VIOLATION` instead; no 15-minute stage.

---

## 6. Spec conflicts (product decision required)

These are documentation/requirements conflicts, not just code bugs. **Do not “fix” until product chooses the winning source.**

### 6.1 34-hour restart 1–5 AM (highest conflict)

| Source | Position |
|--------|----------|
| **PDF §3.2** | No 1–5 AM requirement — *do not implement* |
| **`docs/regulatory-scope.md` §5** | Requires 34h OFF/SB **including two home-terminal 1:00–5:00 AM periods** |
| **Code** (`replay.py`, `state_machine.py`, `calculators.py`) | Implements 1–5 AM gate; emits `RESTART_INVALID`; weekly reset withheld if gate fails |
| **ADR-005** | Does not prescribe 1–5 AM; defines UTC storage + home-terminal for daily grid boundaries |
| **Launch docs** (`docs/launch/02-core-rule-engine.md`, tests) | Still describe two consecutive 1–5 AM periods |

**Recommendation:** Product explicitly selects PDF (current federal restart as stated in the attached spec) **or** keeps the legacy 1–5 AM gate and updates the PDF / ADR trail. Engine tests currently encode the 1–5 AM behavior (`tests/unit/test_restart_weekly.py`).

### 6.2 Split sleeper “enforced” vs stub

| Source | Position |
|--------|----------|
| **PDF §3.4** | Full 7+3/8+2 rematch + look-back |
| **`regulatory-scope.md` §6** | Lists Split Sleeper as enforced |
| **Code** | Flag / pending block only; no rematch |

### 6.3 Timezone math (PDF §8.1 vs ADR-005 / implementation)

| Source | Position |
|--------|----------|
| **PDF §8.1** | All HOS math in home-terminal TZ; “Never mix UTC into limit calculations; convert once at ingestion.” |
| **ADR-005** | UTC storage; home-terminal for daily 24h log boundaries; event-location for display |
| **Code** | Interval math in UTC; home-terminal used mainly for 1–5 AM overlap and dashboard day grids |

This is a design tension (ADR-005 is accepted in-repo). Align PDF wording with ADR-005 or change ingestion/engine policy — do not silently diverge further.

### 6.4 Severity vocabulary — **mapped in `@2.1.0`**

PDF: `ADVISORY` / `SERIOUS` / `CRITICAL`.  
Code/notifier: `WARNING` / `VIOLATION` / `CRITICAL` (CRITICAL now emitted for >15 min driving/duty overage).  
v1 reporting mapping: see Appendix C (enum names kept; PDF labels are documentation only).

### 6.5 Short-haul / TX rulesets vs exclusions

`regulatory-scope.md` **Explicit Exclusions** lists short-haul 150 air-mile (§395.1(e)) and intrastate non-federal variations.  
PDF **requires** Rulesets B/C/D for the client corridor.  
v1 product scope must reconcile “excluded in regulatory-scope” vs “required in HOS-Regulations.pdf.”

---

## 7. Recommended developer actions (by regulatory risk)

Ordered for false positives / false negatives first.

| Priority | Action | Risk addressed |
|----------|--------|----------------|
| **P0** | Product decision: keep or remove 1–5 AM restart gate; update `regulatory-scope.md` + PDF acceptance accordingly; if removing, delete/repurpose `RESTART_INVALID` and simplify `is_valid_restart_period` | False weekly violations + bogus restart alerts |
| **P0** | Fix 14h to wall-clock from shift start; violate only on **Driving** after hour 14 | Systematic DUTY_WINDOW FN/FP |
| **P0** | Remove YM from `_DRIVING_STATUSES` (keep in duty/weekly); add abuse heuristics later | False 11h / break violations |
| **P1** | Reset break accumulator on any non-driving ≥30 min (include ON not driving) | False REST_BREAK |
| **P1** | Pass `as_of=now` (or equivalent truncate) in sweeper so open segment accrues | Live under-alert |
| **P1** | Implement split-sleeper pairing + 14h exclusion + look-back rematch per §3.4 | False 14h/11h; scope claim gap |
| **P2** | ~~Align severity with PDF §8.3 (60m ADVISORY / 90% cycle / CRITICAL >15m overage)~~ — done in `@2.1.0` | Report/notifier mismatch |
| **P2** | Ruleset selection + B/C/D backlog per client authority mix | Missing regimes |
| **P3** | Form & manner + PC/YM abuse + adverse/16h annotations | SMS BASIC / false-positive suppression |
| **P3** | Design-doc `WARNING_15M` stages if product still wants staged Twilio locks | Product/UX only |

---

## Appendix A — Key code map

| Concern | Primary files |
|---------|----------------|
| Alert emission | `app/domains/engine/calculators.py` |
| Clocks / statuses | `app/domains/engine/state_machine.py` |
| Weekly + restart + truncate | `app/domains/engine/replay.py` |
| Orchestration | `app/domains/engine/rule_pack.py` |
| Live evaluate | `app/domains/engine/sweeper.py` |
| Types | `app/domains/engine/schemas.py` |
| In-repo scope | `docs/regulatory-scope.md` |
| Timezone ADR | `docs/adrs/ADR-005-three-tier-timezone-policy.md` |
| Determinism / pack version | `docs/adrs/ADR-004-rule-pack-semver-and-determinism.md` |
| Spec PDF | `docs/audits/HOS-Regulations.pdf` |

## Appendix B — Pass/fail rollup

| Alert | Verdict (at audit time `@1.3.0`) | Notes as of `@2.1.0` |
|-------|----------------------------------|----------------------|
| `DRIVING_LIMIT` WARNING | FAIL (partial) — threshold + YM | Threshold → 60m; YM fixed in `@2.0.0` |
| `DRIVING_LIMIT` VIOLATION | FAIL (partial) — YM + no CRITICAL | CRITICAL when overage >15m |
| `DUTY_WINDOW` WARNING | FAIL — window shape | Wall-clock + 60m warn in `@2.0.0`/`@2.1.0` |
| `DUTY_WINDOW` VIOLATION | FAIL — shape + non-driving fire | Fixed `@2.0.0`; CRITICAL >15m `@2.1.0` |
| `REST_BREAK` WARNING | FAIL (partial) — reset + YM | Reset fixed `@2.0.0`; warn@60m `@2.1.0` |
| `REST_BREAK` VIOLATION | FAIL (partial) — reset + status check | Fixed `@2.0.0`; stays VIOLATION |
| `WEEKLY_CYCLE` WARNING | FAIL (partial) — 90% vs 2h; restart gate | 90% warn `@2.1.0`; restart Phase 7 |
| `WEEKLY_CYCLE` VIOLATION | FAIL (partial) — restart gate; status check | Restart Phase 7 gated |
| `RESTART_INVALID` VIOLATION | FAIL — should not exist per PDF; obsolete 1–5 AM | Unchanged (Phase 7) |

## Appendix C — PDF §8.3 ↔ enum mapping (`fmcsa-us-property@2.1.0`)

| PDF | Engine enum | Implementation |
|-----|-------------|----------------|
| ADVISORY | `WARNING` | `WARNING_THRESHOLD_SECONDS = 3600`; weekly warn when remaining ≤10% of cycle limit |
| SERIOUS | `VIOLATION` | Limit reached / ≤15 min overage; missed break; cycle exceeded; `RESTART_INVALID` |
| CRITICAL | `CRITICAL` | `overage_seconds > 900` on `DRIVING_LIMIT` / `DUTY_WINDOW` only |

Enum renames intentionally avoided to keep notifier locks and UI labels stable.

---

*Original audit pass did not modify engine code. Severity remediation landed in pack `@2.1.0`.*
