"""Build docs/report/Progress_Report_1.docx.

Run from anywhere:

    .venv/bin/python docs/report/build_report.py

Needs python-docx (``.venv/bin/pip install python-docx``). The YAML, the code
excerpt and the demo output are read from the repository files at build time,
so the report shows what is actually in the repo. Every number in the text is
traced to its source in docs/report/CLAIMS.md.
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "report" / "Progress_Report_3.docx"

BODY_FONT = "Times New Roman"
CODE_FONT = "Courier New"
REPO_URL = "https://github.com/prashansapkota/agent-sequence-monitor/tree/progress-report"


# --------------------------------------------------------------------------
# Repository excerpts (read, not retyped)
# --------------------------------------------------------------------------


def yaml_excerpt() -> list[str]:
    """sequence_rules of policies/examples/exfiltration.yaml, message: lines removed."""
    lines = (ROOT / "policies/examples/exfiltration.yaml").read_text().splitlines()
    start = lines.index("sequence_rules:")
    return [ln for ln in lines[start:] if "message:" not in ln and ln.strip()]



def code_excerpt() -> list[str]:
    """_Window.add and _Window._evict from src/seqmon/state.py, docstrings and comments removed."""
    src = (ROOT / "src/seqmon/state.py").read_text().splitlines()
    out: list[str] = []
    for name in ("def add(self, event", "def _evict(self, now"):
        i = next(k for k, ln in enumerate(src) if ln.strip().startswith(name))
        out.append(src[i][4:])
        for ln in src[i + 1 :]:
            if ln.strip().startswith("def ") or ln.startswith("    @") or (
                ln and not ln.startswith("        ")
            ):
                break
            s = ln.strip()
            if not s or s.startswith("#") or s.startswith('"""'):
                continue
            out.append(ln[4:])  # drop the class-level indent
    return out


def _wrap(line: str, width: int, indent: int) -> list[str]:
    """Wrap one long output line at a space, continuing with a hanging indent."""
    out = []
    while len(line) > width:
        cut = line.rfind(" ", indent + 1, width)
        if cut <= indent:
            break
        out.append(line[:cut])
        line = " " * indent + line[cut + 1 :]
    return out + [line]


def demo_excerpt(width: int = 106) -> list[str]:
    """Selected lines of docs/evidence/demo_output.txt, padding reduced, lines wrapped."""
    text = (ROOT / "docs/evidence/demo_output.txt").read_text().splitlines()
    keep_steps = {"1", "6", "7", "17", "18", "32"}
    rows: list[list[str]] = []
    tail: list[str] = []
    for ln in text:
        cols = re.split(r"\s{2,}", ln.strip())
        if cols[0] == "step":  # "rows cum.rows" is single-space separated in the header
            rows.append(["step", "per-call", "action", "resource", "rows", "cum.rows",
                         "sequence monitor"])
        elif cols[0] in keep_steps and len(cols) == 7:
            rows.append(cols)
        elif ln.startswith(("Per-call engine alone", "Sequence violation fired",
                            "Session terminated")):
            tail.append(ln)
    widths = [max(len(r[i]) for r in rows) for i in range(6)]
    monitor_col = sum(widths) + 6
    out = []
    for r in rows:
        cells = [r[0].rjust(widths[0]), r[1].ljust(widths[1]), r[2].ljust(widths[2]),
                 r[3].ljust(widths[3]), r[4].rjust(widths[4]), r[5].rjust(widths[5])]
        # Several monitor verdicts on one step: one per line, aligned.
        verdicts = r[6].split(", ")
        out.append(" ".join(cells) + " " + verdicts[0] + ("," if len(verdicts) > 1 else ""))
        out += [" " * monitor_col + v for v in verdicts[1:]]
        if r[0] == "1":
            out.append(" ...  (steps 2-5 omitted)")
        if r[0] == "7":
            out.append(" ...  (steps 8-16 omitted; ESCALATE:resource-scope-drift on each)")
        if r[0] == "18":
            out.append(" ...  (13 more calls refused: session terminated)")
    out.append("")
    for ln in tail:
        out += _wrap(ln, width, 4)
    return out


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def set_run_font(run, name: str, size: float | None = None) -> None:
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), name)
    if size is not None:
        run.font.size = Pt(size)


def setup(doc: Document) -> None:
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Inches(1)
        section.left_margin = section.right_margin = Inches(1)
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(12)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), BODY_FONT)
    pf = normal.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(6)
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE


def para(doc, text: str = "", *, bold_lead: str | None = None, align=None,
         space_after: float | None = None, italic: bool = False, lead: bool = False):
    if lead:  # bold the first sentence as a run-in heading
        first, rest = text.split(". ", 1)
        bold_lead, text = first + ". ", rest
    p = doc.add_paragraph()
    if bold_lead:
        r = p.add_run(bold_lead)
        r.bold = True
        set_run_font(r, BODY_FONT, 12)
    if text:
        r = p.add_run(text)
        r.italic = italic
        set_run_font(r, BODY_FONT, 12)
    if align is not None:
        p.alignment = align
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    return p


def heading(doc, text: str, level: int = 1):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    if level == 2:
        r.italic = True
    set_run_font(r, BODY_FONT, 12)
    p.paragraph_format.space_before = Pt(10 if level == 1 else 6)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True
    return p


def shade(cell, fill: str) -> None:
    tcpr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def borders(table, color: str = "BFBFBF") -> None:
    tblpr = table._element.tblPr
    b = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single")
        e.set(qn("w:sz"), "4")
        e.set(qn("w:space"), "0")
        e.set(qn("w:color"), color)
        b.append(e)
    tblpr.append(b)


def code_block(doc, lines: list[str], size: float = 9.0) -> None:
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    borders(table)
    mar = OxmlElement("w:tblCellMar")
    for side in ("left", "right"):
        m = OxmlElement(f"w:{side}")
        m.set(qn("w:w"), "72")
        m.set(qn("w:type"), "dxa")
        mar.append(m)
    table._element.tblPr.append(mar)
    cell = table.rows[0].cells[0]
    cell.width = Inches(6.5)
    shade(cell, "F2F2F2")
    first = True
    for ln in lines:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        pf = p.paragraph_format
        pf.space_after = Pt(0)
        pf.space_before = Pt(0)
        pf.line_spacing = 1.0
        r = p.add_run(ln if ln else " ")
        set_run_font(r, CODE_FONT, size)
    # Keep the block together on one page.
    for p in cell.paragraphs[:-1]:
        p.paragraph_format.keep_with_next = True


def caption(doc, label: str, text: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(label + " ")
    r.bold = True
    set_run_font(r, BODY_FONT, 11)
    r = p.add_run(text)
    set_run_font(r, BODY_FONT, 11)
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(10)


def listing_caption_above(doc, label: str, text: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(label + " ")
    r.bold = True
    set_run_font(r, BODY_FONT, 11)
    r = p.add_run(text)
    set_run_font(r, BODY_FONT, 11)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_with_next = True


def figure(doc, path: Path, label: str, text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_after = Pt(0)
    p.add_run().add_picture(str(path), width=Inches(5.2))
    caption(doc, label, text)


def simple_table(doc, header: list[str], rows: list[list[str]], widths: list[float]) -> None:
    table = doc.add_table(rows=1 + len(rows), cols=len(header))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    borders(table, "808080")
    for i, h in enumerate(header):
        c = table.rows[0].cells[i]
        shade(c, "E7E6E6")
        c.paragraphs[0].paragraph_format.space_after = Pt(0)
        r = c.paragraphs[0].add_run(h)
        r.bold = True
        set_run_font(r, BODY_FONT, 11)
    for ri, row in enumerate(rows, start=1):
        for ci, val in enumerate(row):
            c = table.rows[ri].cells[ci]
            c.paragraphs[0].paragraph_format.space_after = Pt(0)
            r = c.paragraphs[0].add_run(val)
            set_run_font(r, BODY_FONT, 11)
    for row in table.rows:
        for ci, w in enumerate(widths):
            row.cells[ci].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


# --------------------------------------------------------------------------
# Report text
# --------------------------------------------------------------------------



def build() -> Path:
    doc = Document()
    setup(doc)

    for line in ("Prashan Sapkota", "Dr. Ning Zhang", "CSCI 412", "Progress Report 3",
                 "October 6, 2026"):
        para(doc, line, space_after=0)
    t = para(doc, "", align=WD_ALIGN_PARAGRAPH.CENTER)
    t.paragraph_format.space_before = Pt(12)
    t.paragraph_format.space_after = Pt(12)
    r = t.add_run("Detecting Cumulative Policy Violations in Autonomous AI Agents")
    r.bold = True
    set_run_font(r, BODY_FONT, 12)

    # 1 ---------------------------------------------------------------------
    heading(doc, "1. Project Overview")
    para(doc,
         "Governance layers for AI agents, such as the Microsoft Agent Governance Toolkit "
         "(AGT), check each tool call against a declarative YAML policy before it runs. When I "
         "read the AGT source (tag v3.5.0), I confirmed that this check is stateless: the "
         "policy evaluator sorts rules by priority, lets the first matching rule decide, and "
         "keeps nothing between calls, and the stateless kernel's docstring says that it "
         "“never looks up prior requests.” A per-call check therefore cannot see harm "
         "that exists only across a sequence of individually allowed calls: a bulk export "
         "assembled from many small reads, a budget drained by many small charges, or an agent "
         "drifting step by step outside its declared task scope. My goal is a Python monitoring "
         "layer, seqmon, that runs beside AGT's per-call engine, consumes the same stream of "
         "intercepted calls, keeps per-session state and responds when a sequence rule is "
         "broken, without replacing per-call enforcement.")

    # 2 ---------------------------------------------------------------------
    heading(doc, "2. Work Completed")
    para(doc,
         "Progress Report 2 (September 28) covered the core monitor, the 30 labelled "
         "scenarios and the first experiments. Since then I aligned the rule format with AGT, "
         "rebuilt the runtime interfaces, added an AGT adapter and a demo, and ran an internal "
         "review and an adversarial test pass. This section describes the system as it now "
         "stands.")
    para(doc,
         "Rule format. Sequence rules live in the same YAML file as AGT's per-call rules, under "
         "a separate top-level key, sequence_rules, with an optional sequence_defaults block. I "
         "chose this layout after reading AGT's schema. AGT's PolicyDocument has top-level "
         "version, name and description fields and a rules list, and AGT's JSON schema already "
         "keeps one non-per-call section, a2a_conversation_policy, beside rules. "
         "sequence_rules follows that precedent, sequence_defaults mirrors AGT's defaults "
         "block, and responses accept AGT's verbs warn, pause and break as well as log, "
         "escalate and terminate. One file can therefore drive both layers: AGT's pydantic "
         "loader accepts my combined example files and ignores the sequence keys, and seqmon "
         "ignores AGT's rules. The rule bodies themselves are my own design. The parser "
         "(src/seqmon/spec.py) supports the four constraint types of the proposal: cumulative "
         "aggregates (sum, count or distinct resources) over a rolling window; rate limits per "
         "tool or across all tools; ordering (B must not follow A within a window); and scope, "
         "where more than max_outside accesses outside a declared resource allowlist within a "
         "window counts as drift. Every error is a SpecError that starts with the field path "
         "(for example sequence_rules[2].window), and unknown keys are rejected so a typo "
         "cannot silently create a different rule. The format is documented in "
         "docs/rule_format.md, with four example policies in policies/examples/.",
         lead=True)
    para(doc,
         "Runtime components. The runtime is 2,133 lines of Python in src/seqmon/. "
         "ToolCallEvent (events.py) carries the action, session, timestamp, resource, a "
         "magnitude and named attributes such as records or cost_usd. The state accumulator "
         "(state.py) keeps one sliding window per rule per session: a deque of events with a "
         "running sum and a per-resource counter, evicted by timestamp. The incremental "
         "evaluator (IncrementalEvaluator.on_action in evaluator.py) takes one permitted call, "
         "updates the windows of the rules it matches, checks them and returns any violations. "
         "The response layer (response.py) maps each violation to LOG, ESCALATE or TERMINATE "
         "and calls pluggable EscalationHook and TerminationHook objects; an approved "
         "escalation re-arms the rule so the next breach asks again. SequenceMonitor "
         "(monitor.py) wraps these components. src/seqmon/adapters/ defines the PerCallEngine "
         "and CallFeed interfaces a real AGT hook would implement, a FakePerCallEngine that "
         "re-implements AGT's flat YAML evaluation, and a FakeAGTInterceptor that chains it "
         "with the monitor.",
         lead=True)
    para(doc,
         "Design decisions. Incremental updates: the running sum and the distinct-resource "
         "count change only when an event enters or leaves a window, so no check rescans the "
         "history and the cost per event depends on the number of rules, not on session "
         "length (Figure 1). To check that shortcut, a naive evaluator (bench/naive.py) "
         "recomputes every rule from its definition, and property tests compare the "
         "incremental evaluator with an independently written reference on random policies "
         "and traces. Bounded deque: each window holds only events inside its span, plus a "
         "hard cap, max_events_per_window (10,000 by default). I kept a manual cap rather than "
         "deque(maxlen=...) because an event dropped by the cap must also be subtracted from "
         "the running sum and counted; maxlen would discard it silently. The bound is per "
         "session (rules × cap events); across sessions, idle sessions expire only when a "
         "clock or a shared time base is configured (Section 4).",
         lead=True)
    para(doc,
         "AGT study and evaluation harness. I cloned AGT outside this repository and recorded "
         "what its source shows in docs/agt_integration.md. The interception point is "
         "ToolCallInterceptor.intercept, and a CompositeInterceptor returns the first deny, so "
         "an interceptor placed last sees only allowed calls, which is what the monitor "
         "assumes. scripts/check_agt_compat.py runs AGT's real evaluator: it agreed with "
         "FakePerCallEngine on all 36 calls compared, but only 4 of them match a rule, so this "
         "is weak evidence. For evaluation I built a labelled scenario format with 30 seeded "
         "scenarios (11 adversarial, 10 designed to evade the example policy, 9 benign), a "
         "scorer, four experiments run by experiments/run_all.py (detection, damage before "
         "detection, threshold sweep, overhead) and a demo, scripts/demo_trace.py.",
         lead=True)
    simple_table(
        doc,
        ["Proposal weeks", "Planned", "Status on October 6, 2026"],
        [
            ["1–2", "Rule format", "Done."],
            ["3–5", "State, incremental evaluator, responses",
             "Done and reviewed. Ordering lacks the “B must be preceded by A” form."],
            ["6–8", "Harness and scenarios",
             "30 scripted scenarios done; LLM-driven harness not started."],
            ["9–11", "Experiments", "First pass on scripted traces."],
            ["Last", "Scope drift", "Evaluator built 2026-09-15, ahead of plan."],
            ["—", "AGT integration",
             "Interfaces only; per-call engine is SIMULATED (FakePerCallEngine)."],
        ],
        [1.1, 2.2, 3.2],
    )
    para(doc,
         "Compared with the proposal timeline I am ahead of schedule overall: work the plan placed "
         "in weeks 3 to 11 already exists in a first form. There are three deviations. Scope drift "
         "was built first instead of last: its evaluator was already in the first commit "
         "(96b5b9e). The scenarios are hand-written seeded traces, not traces from the planned "
         "LLM agent with mock tools. And AGT's per-call engine is simulated: the bench scripts "
         "use hard-coded allow verdicts and the demo uses FakePerCallEngine. I am behind on the “must be "
         "preceded by” ordering form.")

    # 3 ---------------------------------------------------------------------
    heading(doc, "3. Evidence of Progress")
    para(doc,
         "Code: " + REPO_URL + " (package src/seqmon/). The work in this report is in commits "
         "10dfb4c to 4469ce7 on that branch (October 6, 2026).")
    para(doc,
         "Listing 1 is an example policy. Each tracked query passes a per-call check; the "
         "violation exists only in the hourly sum of rows or in the order “read customer "
         "data, then send externally.” Listing 2 is the core of the incremental design: "
         "adding an event and evicting old ones touch only running totals.")
    listing_caption_above(doc, "Listing 1.",
                          "Sequence rules in policies/examples/exfiltration.yaml "
                          "(message lines omitted).")
    code_block(doc, yaml_excerpt())
    listing_caption_above(doc, "Listing 2.",
                          "_Window.add and _Window._evict in src/seqmon/state.py "
                          "(docstrings and comments omitted).")
    code_block(doc, code_excerpt())
    para(doc, "", space_after=0)
    para(doc,
         "Listing 3 is the demo. A 32-call synthetic slow-exfiltration trace runs through "
         "FakeAGTInterceptor with one combined policy file. Its per-call rule denies any single "
         "query over 1,000 rows, but the agent pages 400 rows at a time, so every call is "
         "allowed. The scope rule escalates at step 7, and the cumulative rule terminates the "
         "session at step 17 after 5,260 rows; the other 15 calls, including the external "
         "send, are refused. Without the sequence layer all 32 calls run (10,860 rows). The "
         "demo has no human approver, so its 10 escalations are auto-approved, and it says so. "
         "Simulated comparison: the per-call verdicts in this demo come from FakePerCallEngine, "
         "my re-implementation of AGT's flat YAML evaluation, not from the AGT toolkit itself. "
         "It agreed with AGT's real PolicyEvaluator on all 36 calls I compared, but only 4 of "
         "those matched a rule, so the per-call side should be read as simulated.")
    listing_caption_above(doc, "Listing 3.",
                          "Excerpt of docs/evidence/demo_output.txt (rows omitted, column "
                          "padding reduced, long lines wrapped).")
    code_block(doc, demo_excerpt(), size=7)
    para(doc, "", space_after=0)
    para(doc,
         "Testing and results. The suite has 426 tests in 12 files: 412 pass and 14 are "
         "strict expected failures tied to nine bugs logged in BUGS.md. Statement coverage of "
         "seqmon is 96% (823 statements, 33 missed; docs/evidence/coverage.txt), and ruff and "
         "strict mypy report no issues. On the scripted suites the monitor detected 11 of 11 "
         "adversarial scenarios and 2 of 10 evasion scenarios, with no alert on the 9 benign "
         "ones. In experiments/overhead.py, a naive evaluator that rescans the "
         "history took 10,491 µs per event at 100,000 events against 3.6 µs for the "
         "incremental one. All timings are single uncontrolled runs on one machine.",
         lead=True)
    figure(doc, ROOT / "bench/results/overhead.png", "Figure 1.",
           "Monitor overhead against session length (bench/overhead.py; 6-rule example "
           "policy, 1 event/s, Apple M1, CPython 3.12.7). Left: evaluation time stays between "
           "2.3 and 2.6 µs per event from 100 to 100,000 events. Right: memory reaches "
           "3,369 KiB because the policy's one-day window has not yet filled; the 12,050 "
           "retained events are within the 6 × 10,000 bound.")

    # 4 ---------------------------------------------------------------------
    heading(doc, "4. Challenges")
    para(doc,
         "Most evasions succeed, and the memory cap is an attack surface. Eight of the ten "
         "evasion scenarios are missed: they stay under thresholds, hop between windows, split "
         "across sessions, pace sends or strays just outside a window, or stay under the daily "
         "budget, which are limits of fixed-threshold, fixed-window, per-session rules. One, "
         "backstop-flush, pads the window with about 1,000 zero-row calls so the 10,000-event "
         "cap pushes the damaging events out. This is open; the options (never shed events "
         "that carry magnitude, or alert when shedding starts) are in OPEN_QUESTIONS.md.",
         lead=True)
    para(doc,
         "AGT is a moving target. The AGT commit pinned in the README, 5ed63c6e, is not in the "
         "public repository, so I used tag v3.5.0. AGT's main branch removed this YAML format "
         "on 2026-07-29, AGT's JSON schema rejects my combined files because it forbids "
         "unknown top-level keys, and AGT's tool-call request carries no session id, timestamp "
         "or result size. These are open questions 2 to 5 and they block the real hook.",
         lead=True)
    para(doc,
         "An internal review found runtime design flaws. A review on 2026-10-06 (REVIEW.md) "
         "showed that out-of-order timestamps corrupted windows (one event at t = 1e12 emptied "
         "every window), that memory was bounded per session but not per process, that one "
         "approval muted an escalating rule for the rest of the session, and that reusing "
         "AGT's action key turned “action: deny” into a filter matching nothing. I "
         "fixed all four with regression tests (late events are clamped, idle sessions expire, "
         "approval re-arms the rule, unknown keys are rejected). A second round found that my "
         "idle-session sweep let one session's far-future timestamp empty another session. "
         "The sweep now runs only with a clock or an explicit shared-time-base flag, so by "
         "default sessions that never return are kept (BUG-009). After both rounds every "
         "detection result file was byte-identical to the run before the fixes.",
         lead=True)
    para(doc,
         "Bugs from the test pass are still open. New boundary, property and state-bound "
         "tests exposed nine bugs. The most serious, BUG-006, is that a rule needing more "
         "events than the per-window cap can never fire, and nothing reports it. BUG-001/002 "
         "are floating-point drift in the running sum: 500 charges of $0.10 fire a $50 limit "
         "they only reach. Earlier engineering problems are resolved: the parser accepted "
         "booleans as numbers (now a SpecError), tests failed outside the repository root "
         "(paths now relative to __file__), and macOS hides the venv's editable-install .pth "
         "file, so scripts and pytest put src/ on the path.",
         lead=True)

    # 5 ---------------------------------------------------------------------
    heading(doc, "5. Next Steps")
    para(doc, "Before the next report I plan to deliver the following.")
    steps = [
        ("Ordering constraints. ",
         "Implement “B must be preceded by A,” storing only the last enabling "
         "timestamp (review item MINOR 4), with tests against the naive oracle; fix BUG-006 by "
         "rejecting rules that cannot fire under the cap; decide the backstop-flush policy."),
        ("Real AGT baseline. ",
         "Replace FakePerCallEngine with AGT's real PolicyEvaluator and run AGT alone and AGT "
         "plus seqmon on identical trajectories, reporting records exposed or budget spent "
         "before intervention alongside detection delay."),
        ("Response hooks wired to an AGT adapter. ",
         "A ToolCallInterceptor placed last in a CompositeInterceptor, with TerminationHook "
         "refusing the session's later calls and EscalationHook holding for approval, and a "
         "clock on the live path. This first needs answers to open questions 2 to 5 (target "
         "AGT version, source of session id and timestamp, post-execution row counts)."),
        ("Mock-tool LLM harness and benign suite. ",
         "An LLM agent acting through mock tools, with calls recorded in the existing scenario "
         "format, and a benign suite larger than 9 scenarios so a false-positive rate can be "
         "estimated."),
        ("Open bugs and repository state. ",
         "Fix BUG-001/002 (integer minor units or math.fsum) and the validation gaps BUG-003 "
         "to BUG-005 and BUG-007/008; write the comparison with AGT's own stateful limiters "
         "(MCPSlidingRateLimiter, BudgetTracker)."),
    ]
    for i, (lead, text) in enumerate(steps, 1):
        p = para(doc, text, bold_lead=f"{i}. {lead}")
        p.paragraph_format.left_indent = Inches(0.25)
    para(doc,
         "Scope drift stays last, as an optional extension. Its evaluator exists, so the remaining "
         "work is hardening (the slow-drift evasion and the shadowing of the distinct-table "
         "rule) after the items above.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    return OUT


if __name__ == "__main__":
    print(build())
