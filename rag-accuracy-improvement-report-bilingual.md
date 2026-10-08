# RAG Accuracy Improvement Report / RAG 準確度提升報告

## 1. Executive Summary / 執行摘要

### 中文

這次修改把原本「每張發票都直接加入 RAG 範例」改成較安全的兩階段流程。系統先做一次不含 RAG 的基準抽取，再使用既有的 Python 驗證規則檢查欄位完整度、總額證據與明細加總。只有結果有缺漏或證據不足時，系統才檢索一個相關範例並做第二次抽取。第二次結果必須在證據排名上嚴格優於第一次才會被採用；相同或更差時保留原結果。

這項修改提高的是可靠性與錯誤恢復能力。現有三張評估發票的 baseline 已經是 100%，因此數字不可能再高於 100%。最新測試確認 baseline 與 always-RAG 仍為 15/15，沒有準確度退步；要證明實際提升，仍需加入更多困難且未參與開發的測試發票。

### English

This change replaces always-on RAG in the application pipeline with a safer two-pass workflow. The system first extracts without RAG and applies the existing deterministic Python validation. It retrieves one relevant example and makes a second extraction only when fields or supporting evidence are weak. The retry is adopted only when its evidence-based rank is strictly better; ties and weaker results preserve the baseline.

The improvement targets reliability and error recovery. The current three-document benchmark already has a 100% baseline, so it cannot numerically exceed 100%. The latest evaluation kept both baseline and always-RAG at 15/15 correct fields. Demonstrating an empirical gain requires a larger held-out set of difficult invoices.

## 2. Previous Risk / 原本的風險

### 中文

原本 `main.py --rag` 對每張文件都加入範例。即使第一次抽取已經完整，模型仍可能受到不相關範例影響、複製範例值，或增加不必要的延遲。系統也沒有比較加入 RAG 前後哪一個結果比較可靠。

### English

Previously, `main.py --rag` added an example to every document. Even when the original extraction was complete, an irrelevant example could distract the model, encourage value copying, or add unnecessary latency. The pipeline did not compare the baseline and RAG answers before selecting one.

## 3. New Processing Flow / 新處理流程

1. **Baseline extraction / 基準抽取** — Ollama reads the current invoice without a retrieved example.
2. **Deterministic validation / 規則驗證** — Python checks status, missing fields, amount evidence, line-item reconciliation, and the validation score.
3. **Conditional retry / 條件式重試** — RAG runs only for `NeedsReview`, missing fields, an unverified amount, or unknown/short reconciliation.
4. **Evidence comparison / 證據比較** — Both verdicts are ranked by validation status, amount evidence, reconciliation, score, and completeness.
5. **Safe selection / 安全選擇** — The RAG answer replaces the baseline only for a strict improvement. A tie keeps the baseline.

## 4. Safety Rules / 安全規則

### 中文

提示詞現在明確規定所有輸出值只能來自目前文件。檢索範例只能用來理解格式與標籤，不可複製供應商、發票號碼、日期、金額、幣別或明細。找不到的值必須保持空值，不可猜測。最終選擇由 Python 證據規則決定，不由 LLM 自己聲稱的信心決定。

### English

The prompt now states that every output value must come from the current document. Retrieved examples may demonstrate structure and labels, but their vendor, invoice number, date, amount, currency, and items must never be copied. Missing values must remain missing rather than be guessed. Python evidence rules select the final answer; the LLM does not grade itself.

## 5. Code Changes / 程式修改

- `main.py`: per-call RAG control, retry trigger, evidence ranking, safe result selection, and clearer runtime logs.
- `evaluation/run_eval.py`: controlled baseline, always-RAG, and selective-RAG evaluation modes.
- `tests/test_selective_rag.py`: tests for retry conditions, strict improvement, tie handling, weaker-result rejection, and the RAG override.
- `README.md` and `rag-poc.md`: updated commands, architecture, limitations, and verified results.

## 6. Verification Results / 驗證結果

### 30-document benchmark / 30 張文件評估

The extended benchmark contains 30 synthetic held-out, text-based PDFs across six layouts.
None of their vendors or invoice values appears in the RAG example repository. Fallback
repairs were disabled, and each mode ran three times with `llama3.2:latest`.

擴充評估包含 30 張獨立的合成文字型 PDF，涵蓋六種版面。測試文件的 vendor 與
invoice values 都沒有出現在 RAG example repository。三種模式都關閉 fallback，並以
`llama3.2:latest` 各執行三次。

| Metric / 指標 | Baseline | Always-RAG | Selective-RAG |
|---|---:|---:|---:|
| Field accuracy / 欄位準確率 | 91.3% | 94.0% | 94.0% |
| Correct fields / 正確欄位 | 137/150 | 141/150 | 141/150 |
| Document exact match / 整張全對 | 73.3% | 80.0% | 80.0% |
| Validation pass rate | 36.7% | 33.3% | 43.3% |
| Mean latency / 平均延遲 | 5.230 s | 3.781 s | 8.606 s |
| Selective retries | — | — | 19/30 per run |
| Selective adoptions | — | — | 11/30 per run |

All three runs produced identical accuracy values. Both RAG modes improved four fields per
run and improved three documents without making any document less accurate. Compared with
baseline, the repeatable changes were:

- Vendor name: 86.7% → 90.0% (+3.3 percentage points)
- Invoice number: 80.0% → 83.3% (+3.3 points)
- Total amount: 90.0% → 96.7% (+6.7 points)
- Overall field accuracy: 91.3% → 94.0% (+2.7 points)
- Document exact match: 73.3% → 80.0% (+6.7 points)
- Three documents improved, zero became worse, and 27 were unchanged in every run.

三輪的準確率結果完全相同。兩種 RAG 模式每輪都多修正四個欄位，改善三張文件，
沒有任何文件變差。這表示在此合成 benchmark 上，RAG 的準確度提升是可重複的。

Selective-RAG reached the same extraction accuracy as always-RAG and had the highest
validation pass rate. Its latency was higher because 19 weak documents required both a
baseline call and a retry. The always-RAG latency happened to be lower during these runs,
but local warm-up and machine load make cross-run latency less reliable than the accuracy
comparison.

### Other verification / 其他驗證

- RAG-focused automated tests: 18 passed.
- Extended fixture integrity tests verify 30 PDFs, frozen ground truth, and no vendor overlap.
- Isolated application workflow: 4/4 documents processed.
- Original working database SHA-1 remained unchanged.

The earlier full-suite run produced 653 passed, 2 skipped, and 7 failures caused by mutable
UI/database state. Those failures are separate from the RAG evaluation.

## 7. What the Result Means / 結果代表什麼

### 中文

在新的 30 張合成 held-out benchmark 上，可以合理地說 RAG 將欄位準確率從 91.3% 提升到 94.0%，提高 2.7 個百分點；整張文件全對率從 73.3% 提升到 80.0%，提高 6.7 個百分點，而且三輪結果一致。這是測試範圍內的證據，不能直接等同於 production accuracy，因為資料仍是合成、文字型 PDF，尚未涵蓋 OCR 與真實供應商分布。

### English

On the new 30-document synthetic held-out benchmark, RAG improved field accuracy from 91.3% to 94.0%, a 2.7 percentage-point gain. Document exact match improved from 73.3% to 80.0%, a 6.7-point gain, with identical results across all three runs. This is evidence within the benchmark scope, not a production-accuracy claim: the documents are synthetic, text-based PDFs and do not cover OCR or the full real-vendor distribution.

## 8. Recommended Next Experiment / 下一個建議實驗

Create a held-out set of at least 30–50 invoices covering unfamiliar vendors, alternative total labels, missing fields, tax and discount cases, multi-page tables, OCR noise, and image-only PDFs. Freeze the ground truth before testing. Run baseline, always-RAG, and selective-RAG at least three times each, then report field accuracy, document pass rate, false automation rate, retry rate, adoption rate, and latency distribution.
