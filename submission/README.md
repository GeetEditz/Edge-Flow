# Submission package — Distributed CDN Simulator

Computer Networks Laboratory (CSE20110) · Laboratory Continuous Assessment 3 · Group No. 2 ·
Faculty: Dr. Swati · Academic Year 2026–2027

| File | Description |
|---|---|
| `Distributed_CDN_Simulator_Final_Report.docx` | Completed report, edited from a copy of the original Word template (editable) |
| `Distributed_CDN_Simulator_Final_Report.pdf` | PDF exported from the DOCX with Microsoft Word (fields and TOC updated), 35 pages |
| `Distributed_CDN_Simulator_Final_Presentation.pptx` | 8-slide presentation, edited from a copy of the original PowerPoint template (editable, speaker notes included) |
| `figures/` | Diagrams, charts (generated from `evidence/experiment_results.json`) and real dashboard screenshots |
| `evidence/` | Test outputs, experiment results, browser-check log, demo rehearsal log, validation report |

The original templates in the repository root were not modified (SHA-256 checked by the validator).

## Team

| Sr. No. | Student Name | PRN |
|---|---|---|
| 1 | More Geet Dattatraya | 1262250006 |
| 2 | Shinde Soham Manoj | 1262250009 |
| 3 | Jagtap Prasad Sachin | 1262250018 |
| 4 | Gaikwad Shreyash Sachin | 1262250005 |

## Fields the team must complete before submission

These could not be verified from the repository and are left as clearly marked placeholders
(yellow highlight in the DOCX, orange text in the PPTX):

* **Panel** and **Batch** — cover page, certificate, slide 1
* **Submission date** — cover page; **presentation date** — slide 1
* **Individual contributions** — report Appendix C (Table C.1) and slide 7 (no version-control history exists)
* **Signatures** — certificate (faculty) and AI-usage declaration table (students); intentionally blank

Also review: the certificate keeps the template's wording "Third Year B.Tech. Computer Science and
Engineering", while the cover uses "Third Year B.Tech. Computer Engineering and Technology" (also from the
template). Adjust if your department requires one form.

## Regenerating

From the repository root (Windows; Microsoft Word/PowerPoint and Google Chrome are used for PDF export and
screenshots; the backend on :8000 and frontend on :5173 must be running for screenshots and the UI check):

```bash
python -m venv .venv-docs
.venv-docs\Scripts\python -m pip install python-docx==1.1.2 python-pptx==1.0.2 matplotlib==3.9.2 pymupdf==1.24.14 websockets==13.1
.venv-docs\Scripts\python scripts\capture_screenshots.py      # real dashboard screenshots
.venv-docs\Scripts\python scripts\make_figures.py             # diagrams + charts from evidence JSON
.venv-docs\Scripts\python scripts\generate_report.py          # DOCX + PDF
.venv-docs\Scripts\python scripts\generate_presentation.py    # PPTX
.venv-docs\Scripts\python scripts\validate_submission.py      # checks (40/40 passed on 2026-10-20)
```

`scripts/ui_check.py` re-runs the scripted browser checks and `scripts/render_pages.py` renders a PDF into
contact sheets for visual review.

## Evidence status

* Automated tests: 79 backend + 10 frontend passed (`evidence/backend_pytest.txt`, `evidence/frontend_vitest.txt`).
* Scripted dashboard checks: 16/16 passed, no console errors (`evidence/ui_check.txt`).
* Experiments A–D measured in Local Mode on 2026-10-20 (`evidence/experiment_results.json`); link conditions
  are simulated. Experiment D was re-run after a bug fix (see report Table 5.1).
* Demo sequence rehearsed against the running system (`evidence/demo_rehearsal.txt`).
* **Not executed:** Mininet topology (requires Linux) and Wireshark captures (not installed). The report marks
  these as not tested.
* References were compiled from standard bibliographic details (IETF RFCs, ACM/IEEE DOIs, official
  documentation URLs) but were not re-checked online in this session; spot-check them before submission.
