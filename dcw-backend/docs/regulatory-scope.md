# Regulatory Scope Specification (49 CFR Part 395)

DCW Version 1 enforces U.S. FMCSA Property-Carrying Interstate Rules
(`fmcsa-us-property@2.5.0`) for drivers on **Ruleset A**. Pack `@2.5.0` drops
the obsolete two home-terminal 1–5 AM periods gate on 34h restart: ≥34h
consecutive OFF/SB resets the weekly cycle, and `RESTART_INVALID` is no longer
emitted (enum retained for historical audits). Pack `@2.4.0` added Phase 6
form & manner / exception findings; `@2.3.0` added short-haul and Texas types
for Rulesets B/C/D (`fmcsa-us-short-haul@1.0.0`, `tx-intrastate@1.0.0`,
`tx-short-haul@1.0.0`).

## Driver profile (ruleset selection)

Per-driver, tenant-scoped `driver_profiles` rows drive daily ruleset selection
(PDF §2). Missing rows default to:

| Field | Default |
|-------|---------|
| `operating_authority` | `INTERSTATE` |
| `short_haul_eligible` | `false` |
| `cdl_required` | `true` |
| `cycle` | `70_8` |
| `home_terminal_timezone` | `DEFAULT_HOME_TERMINAL_TIMEZONE` |
| `work_reporting_location` | null (required for Ruleset B; missing → fail closed) |
| `vehicle_weight_class` / `hazmat_placard` | optional / null |

**Daily router:** INTERSTATE → base **A**; TX_INTRASTATE → base **C**. When
`short_haul_eligible` (and day exemption not explicitly force-failed) → **B** or **D**.

| Ruleset | Pack module | Status |
|---------|-------------|--------|
| A — Federal interstate | `fmcsa_us_property` | Implemented (`fmcsa-us-property@2.5.0`) |
| B — Federal 150 air-mile short-haul | `fmcsa_us_short_haul` | Implemented (`fmcsa-us-short-haul@1.0.0`) |
| C — Texas intrastate | `tx_intrastate` | Implemented (`tx-intrastate@1.0.0`, Phase 5) |
| D — Texas short-haul | `tx_short_haul` | Implemented (`tx-short-haul@1.0.0`, Phase 5) |

`RULESET_UNSUPPORTED` remains available for future unimplemented regimes
(dashboard/audit `WARNING` only; not telephony).

## Ruleset B — Federal 150 air-mile short-haul (§ 395.1(e) / PDF §4)

Pack id / SemVer: `fmcsa-us-short-haul@1.0.0`. Selected when
`operating_authority=INTERSTATE` and `short_haul_eligible=true`.

### Exemption conditions (fail closed)

All of the following must hold for the current duty window; otherwise the
exemption is lost for the day:

1. **Work-reporting location** present on the driver profile (lat/lon).
2. **GPS breadcrumbs** available during the duty window (mapped from
   `gps_breadcrumbs` at the sweeper/repository boundary into engine `GpsFix`
   inputs — packs do not query the GPS table directly; see ADR-007).
3. **150 air-mile radius** — every fix in the duty window is ≤ 150 air-miles
   (nautical miles, great-circle) from the work-reporting location.
4. **Return + release** within **14 consecutive hours** when `cdl_required=true`
   (§ 395.1(e)(1)); within **16 consecutive hours** when `cdl_required=false`
   (non-CDL / PDF §4.2). “Returned” means the last duty-window fix is within
   **2 air-miles** of the work-reporting location (depot GPS tolerance).

Missing WRL or breadcrumbs → exemption fails (require RODS / fall back to A).
Never silently pass the radius check.

### Clocks while exemption holds

- Enforce **11-hour driving** and **60/70 weekly** (same calculators as A).
- **Suppress 30-minute break** (`break_required=false`; no `REST_BREAK` findings).
- Duty remaining countdowns track the short-haul **release window** (14h / 16h),
  not the federal 14h on-duty window violation path.
- 34h restart: ≥34h consecutive OFF/SB (no 1–5 AM gate), same as Ruleset A `@2.5.0`.

### When exemption fails

- Fall back to **full Ruleset A** clocks for that evaluation (including break +
  federal 14h duty window).
- Emit `EXEMPTION_LOST` + `RODS_REQUIRED`.
- `selected_ruleset` remains **B** (intended regime) with
  `ruleset_status=IMPLEMENTED` and `ruleset_pack_id=fmcsa_us_short_haul`.

### Rolling 8-in-30 ELD counter

Home-terminal calendar days with exemption failure are persisted in Redis
(`short_haul_fail_days:{tenant}:{driver}`). Rolling count over 30 days drives:

| Failure days in 30 | Severity | Type |
|--------------------|----------|------|
| 5–6 | `WARNING` | `ELD_REQUIRED_8_IN_30` |
| 7–8 | `VIOLATION` | `ELD_REQUIRED_8_IN_30` |
| 9+ | `CRITICAL` | `ELD_REQUIRED_8_IN_30` |

## Ruleset C — Texas intrastate (37 TAC §4.12 / PDF §5)

Pack id / SemVer: `tx-intrastate@1.0.0`. Selected when
`operating_authority=TX_INTRASTATE` and `short_haul_eligible=false` (or
short-haul force-failed via `exemption_ok=False`).

| Clock | Behavior |
|-------|----------|
| Driving | **12h** after **8** consecutive hours OFF/SB/PC (not federal 10h) |
| On-duty | **15h accumulated** ON + Driving + Yard Move (not federal wall-clock 14h). VIOLATION/CRITICAL only while Driving after 15h |
| Weekly | **70h / 7-day only** (no 60/7) |
| Break | **None** — 30-minute break suppressed |
| Restart | ≥34h consecutive OFF/SB/PC resets the 7-day total — **no** federal 1–5 AM gate on TX restart |
| Sleeper split (§5.3) | Two SB periods each ≥2h totaling ≥8h; rematch 12h/15h from end of first period; driving/duty around each period must stay within 12h/15h |

**Severity:** Texas 12h/15h overages are `CRITICAL` (OOS-risk: mandatory 8h roadside OOS per PDF §5.2 / §8.3). ADVISORY `WARNING` within 60 min while driving.

Violation types: `TX_DRIVING_LIMIT`, `TX_DUTY_LIMIT`, plus `WEEKLY_CYCLE` with Texas rule refs.

## Ruleset D — Texas 150 air-mile short-haul (37 TAC §4.12(a)(4)–(5) / PDF §6)

Pack id / SemVer: `tx-short-haul@1.0.0`. Selected when
`operating_authority=TX_INTRASTATE` and `short_haul_eligible=true`.

### Exemption conditions (fail closed)

1. Work-reporting location present.
2. GPS breadcrumbs during the duty window (same `GpsFix` boundary as Ruleset B).
3. **150 air-mile** great-circle radius from work-reporting location.
4. Return + release within **12 consecutive hours** (tighter than federal 14h).
5. **8 consecutive hours off** between tours (TX reset threshold).

### Clocks while exemption holds

- Run **Ruleset C accumulators in parallel** (12h drive / 15h accumulated / 70÷7).
- **RODS relief only** — no break rule (already absent under C).
- On condition fail: still evaluate full C clocks and emit `EXEMPTION_LOST` +
  `RODS_REQUIRED` (`selected_ruleset` remains **D**).

## Rules Enforced (Ruleset A)

1. **11-Hour Driving Limit** (§ 395.3(a)(3)(i)) — Driving status only (Yard Move does **not** consume the 11h clock)
2. **14-Hour Duty Window** (§ 395.3(a)(2)) — Wall-clock from first ON/D/YM after a qualifying ≥10h reset; OFF/lunch/waiting do **not** pause the window; VIOLATION only while Driving after hour 14
3. **Mandatory 30-Minute Rest Break** (§ 395.3(a)(3)(ii)) — Resets on any consecutive **non-driving** ≥30 min (including ON_DUTY); VIOLATION only while Driving after 8h since break
4. **60/70-Hour Weekly Duty Limits** (§ 395.3(b)) — ON + Driving + Yard Move
5. **34-Hour Restart Provision** (§ 395.3(c)) — ≥34 consecutive OFF/SB hours resets the weekly cycle. **No** 1–5 AM home-terminal periods requirement (`fmcsa-us-property@2.5.0`). Restart is a reset mechanism, not a violation source.
6. **Split Sleeper Berth Option** (§ 395.1(g)(1)) — 7+3 / 8+2 pairing (10h total); both qualifying periods excluded from the 14h window; 11h/14h rematched from the end of the first period; look-back when a qualifying berth closes

## Severity Mapping (PDF §8.3)

Engine enums keep `WARNING` / `VIOLATION` / `CRITICAL` names (notifier/UI stable). PDF vocabulary maps as follows:

| PDF §8.3 | `ViolationSeverity` | Thresholds (`fmcsa-us-property@2.5.0`) |
|----------|---------------------|----------------------------------------|
| ADVISORY | `WARNING` | Within **60 min** of 11h driving / 14h duty window / 8h break; weekly cycle used **>90%** (remaining ≤10% of limit); short-haul 8-in-30 at **5+** days; Phase 6 risk findings |
| SERIOUS | `VIOLATION` | Limit reached or ≤15 min overage on driving/duty; missed break; weekly cycle exceeded; `EXEMPTION_LOST` / `RODS_REQUIRED`; 8-in-30 at **7+** days; ELD malfunction >8 days |
| CRITICAL | `CRITICAL` | Overage **>15 min** on federal 11h/14h limits; **any** Texas 12h/15h overage (OOS-risk); 8-in-30 at **9+** days |

Missed break and cycle exceeded stay `VIOLATION` regardless of overage magnitude. `RESTART_INVALID` is legacy-only (not emitted since `@2.5.0`). `RULESET_UNSUPPORTED` and Phase 6 form/exception/abuse findings are dashboard/audit only (not telephony by default).

## Phase 6 — Exceptions + form & manner

Day-level inputs are modeled as `DayAnnotations` (evaluate kwargs / repository stub).
Findings persist on audit `violations` JSONB. Sweeper skips Twilio for
`NON_TELEPHONY_FINDINGS` (same channel as `RULESET_UNSUPPORTED`).

### Exceptions (extended limits + review notices)

| Exception | Trigger | Clock effect | Finding |
|-----------|---------|--------------|---------|
| Adverse driving § 395.1(b) | Manual per-day flag `adverse_driving=True` | **13h** driving / **16h** window for that evaluation | `ADVERSE_DRIVING_USED` (compliance-neutral) |
| 16h short-haul § 395.1(o) | Flag + `prior_five_tours_same_location` + not `used_sixteen_hour_since_restart` | **16h** window; **11h** drive unchanged | `SIXTEEN_HOUR_EXCEPTION` (compliance-neutral) |

§ 395.1(o) is fail-closed without prior-five-tours evidence. Combined adverse + 16h → 13h / 16h and both notices.

### PC / YM abuse heuristics (risk findings)

| Heuristic | Type |
|-----------|------|
| PC cumulative > **3h** | `PC_ABUSE` |
| PC after standard 11h/14h hours exhausted | `PC_ABUSE` |
| PC moves vehicle materially closer to `next_load_location` (when supplied + GPS) | `PC_ABUSE` |
| YM with GPS speed > **32 km/h** (20 mph) | `YM_ABUSE` |

Public-road map matching for YM is not implemented (speed/GPS only).

### Form & manner (§ 395.8)

Evidence fields on `DayAnnotations` (from ELD/raw payloads when available):

| Evidence | Type |
|----------|------|
| `daily_certified=False` | `FORM_AND_MANNER_MISSING_CERT` |
| `missing_required_fields` non-empty | `FORM_AND_MANNER_MISSING_FIELDS` |
| `unassigned_driving_seconds > 0` | `FORM_AND_MANNER_UNASSIGNED_DRIVING` |
| Log edit Driving→OFF/PC/YM | `FORM_AND_MANNER_LOG_EDIT` |
| `eld_malfunction_days > 8` | `FORM_AND_MANNER_ELD_MALFUNCTION` |

## Explicit Exclusions

* Passenger-carrying rules (§ 395.5)
* Agricultural operations exception (§ 395.1(k))
* ELD ruleset-vs-profile mismatch weekly finding (PDF §5.4)
* Non-Texas state intrastate profiles
* Automated road-class detection for YM (highway-speed heuristic only)
* Live day-annotation persistence (evaluate kwargs + repository stub; store TBD)
