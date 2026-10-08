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

### 中文

- RAG 相關自動測試：18 passed。
- 隔離的端到端流程：4/4 文件成功處理。
- 三張完整發票第一次抽取為 1.00，因此跳過 RAG。
- 一張金額標籤有疑問的發票啟動 RAG；RAG 沒有改善證據，因此保留 baseline 並送人工審核。
- 最新受控評估：baseline、always-RAG 與 selective-RAG 全部為 15/15。
- 平均模型延遲：baseline 3.796 秒；always-RAG 4.650 秒；selective-RAG 3.745 秒。
- selective-RAG 在三張完整文件上為 0 次重試、0 次替換，因此避免三次不必要的 RAG 呼叫。
- 完整測試執行結果：653 passed、2 skipped、7 failed。七個失敗都讀取現有 `workflow_platform.db` 的可變 UI 狀態，例如目前沒有待審文件或 outbox 數量不同；RAG 專用測試全部通過，原資料庫雜湊也保持不變。

### English

- RAG-focused automated tests: 18 passed.
- Isolated end-to-end workflow: 4/4 documents processed.
- Three complete invoices scored 1.00 on the first pass and skipped RAG.
- One invoice with ambiguous amount-label evidence triggered RAG; the retry was not stronger, so the baseline remained in human review.
- Latest controlled evaluation: baseline, always-RAG, and selective-RAG all scored 15/15.
- Average model latency: 3.796 seconds baseline, 4.650 seconds always-RAG, and 3.745 seconds selective-RAG.
- Selective-RAG made zero retries and zero replacements on the three complete documents, avoiding three unnecessary RAG calls.
- Full suite: 653 passed, 2 skipped, and 7 failed. The seven failures read mutable UI state from the existing `workflow_platform.db`, such as an empty approval queue or a different outbox count. All RAG-specific tests passed, and the original database hash remained unchanged.

## 7. What the Result Means / 結果代表什麼

### 中文

這次不能宣稱「準確率從 X% 提升到 Y%」，因為測試集太小且 baseline 已達 100%。可以證明的是：新架構能在弱結果時自動嘗試補救，在沒有改善時安全地回退，而且完整結果不需要支付第二次 LLM 呼叫成本。這是可驗證的可靠性提升。

### English

This work cannot honestly claim an increase from X% to Y% because the benchmark is small and its baseline is already 100%. It does prove that weak results receive an automatic recovery attempt, unsuccessful retries safely fall back, and complete results avoid a second LLM call. That is a verified reliability improvement.

## 8. Recommended Next Experiment / 下一個建議實驗

Create a held-out set of at least 30–50 invoices covering unfamiliar vendors, alternative total labels, missing fields, tax and discount cases, multi-page tables, OCR noise, and image-only PDFs. Freeze the ground truth before testing. Run baseline, always-RAG, and selective-RAG at least three times each, then report field accuracy, document pass rate, false automation rate, retry rate, adoption rate, and latency distribution.
