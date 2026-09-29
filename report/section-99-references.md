# References

Every entry below was verified on 16 September 2026 against the work's arXiv abstract page, ACL
Anthology record or publisher record. Author lists are transcribed from those records rather than
written from memory, because a fabricated citation in a report about fabricated field values
would be the worst available irony.

APA formatting is applied. **Page numbers and DOIs still need a final check** against the
publisher record for the four entries that carry them.

Al Hilmi, M. A., Khare, N., Framil Iglesias, N., Cahyanto, K. A., Al Afghani, A., & Yuliadi, M.
(2026). *Tabular PDF information extraction with local LLMs and layout-aware parsing: A
reliability evaluation.* arXiv:2604.00003. https://arxiv.org/abs/2604.00003

Barzelay, U., Azulai, O., Shapira, I., Friedman, I., Abo Dahood, F., Lee, M., & Daniels, A.
(2026). *VAREX: A benchmark for multi-modal structured extraction from documents.*
arXiv:2603.15118. https://arxiv.org/abs/2603.15118

Dong, Y., Ruan, C. F., Cai, Y., Lai, R., Xu, Z., Zhao, Y., & Chen, T. (2024). *XGrammar: Flexible
and efficient structured generation engine for large language models.* arXiv:2411.15100.
https://arxiv.org/abs/2411.15100

Geng, S., Cooper, H., Moskal, M., Jenkins, S., Berman, J., Ranchin, N., West, R., Horvitz, E., &
Nori, H. (2025). *JSONSchemaBench: A rigorous benchmark of structured outputs for language
models.* arXiv:2501.10868. https://arxiv.org/abs/2501.10868

Geng, S., Josifoski, M., Peyrard, M., & West, R. (2023). Grammar-constrained decoding for
structured NLP tasks without finetuning. In *Proceedings of the 2023 Conference on Empirical
Methods in Natural Language Processing* (pp. 10932–10952). Association for Computational
Linguistics. https://aclanthology.org/2023.emnlp-main.674/

Gómez, J., & Sánchez, J. (2026). *Information extraction from electricity invoices with
general-purpose large language models.* arXiv:2604.25927. https://arxiv.org/abs/2604.25927

Huang, Z., Chen, K., He, J., Bai, X., Karatzas, D., Lu, S., & Jawahar, C. V. (2019).
ICDAR2019 competition on scanned receipt OCR and information extraction. In *2019 International
Conference on Document Analysis and Recognition (ICDAR)*. IEEE.
https://doi.org/10.1109/ICDAR.2019.00244 (preprint: arXiv:2103.10213)

Khanchandani, K., Thakur, A., Shetty, A., Reddy, C., & Behera, R. (2025). *Automated invoice data
extraction: Using LLM and OCR.* arXiv:2511.05547. https://arxiv.org/abs/2511.05547

Khang, M., Jung, S. C., Park, S., & Hong, T. (2025). *KIEval: Evaluation metric for document key
information extraction.* arXiv:2503.05488. https://arxiv.org/abs/2503.05488

Knoop, J., & Holtmann, H. (2026). *Private LLM inference on consumer Blackwell GPUs: A practical
guide for cost-effective local deployment in SMEs.* arXiv:2601.09527.
https://arxiv.org/abs/2601.09527

Laatiri, S., Ratnamogan, P., Tang, J., Lam, L., Vanhuffel, W., & Caspani, F. (2023). *Information
redundancy and biases in public document information extraction benchmarks.* arXiv:2304.14936.
Presented at ICDAR 2023. https://arxiv.org/abs/2304.14936

Maiya, A. S. (2025). *OnPrem.LLM: A privacy-conscious document intelligence toolkit.*
arXiv:2505.07672. https://arxiv.org/abs/2505.07672

Park, S., Shin, S., Lee, B., Lee, J., Surh, J., Seo, M., & Lee, H. (2019). *CORD: A consolidated
receipt dataset for post-OCR parsing.* Document Intelligence Workshop at NeurIPS 2019.
https://openreview.net/forum?id=SJl3z659UH

Reddy, A., Walker, T. T., Ide, J. S., & Bedi, A. S. (2026). *The hidden cost of structured
generation in LLMs: Draft-conditioned constrained decoding.* arXiv:2603.03305.
https://arxiv.org/abs/2603.03305

Rombach, A., & Fettke, P. (2024). Deep learning based key information extraction from business
documents: Systematic literature review. *ACM Computing Surveys.*
https://doi.org/10.1145/3749369

Sarmah, B., Zhu, T., Mehta, D., & Pasquali, S. (2023). *Towards reducing hallucination in
extracting information from financial reports using large language models.* Workshop on
Generative AI, 3rd International Conference on AI-ML Systems. arXiv:2310.10760.
https://arxiv.org/abs/2310.10760

Sibue, M., Muñoz Garza, A., Mensah, S., Shetty, P., Ma, Z., Liu, X., & Veloso, M. (2026).
*ExStrucTiny: A benchmark for schema-variable structured information extraction from document
images.* arXiv:2602.12203. https://arxiv.org/abs/2602.12203

Tam, Z. R., Wu, C.-K., Tsai, Y.-L., Lin, C.-Y., Lee, H.-y., & Chen, Y.-N. (2024). Let me speak
freely? A study on the impact of format restrictions on large language model performance. In
*Proceedings of the 2024 Conference on Empirical Methods in Natural Language Processing: Industry
Track* (pp. 1218–1236). Association for Computational Linguistics.
https://aclanthology.org/2024.emnlp-industry.91/

Willard, B. T., & Louf, R. (2023). *Efficient guided generation for large language models.*
arXiv:2307.09702. https://arxiv.org/abs/2307.09702

## Vendor documentation

Ollama. (n.d.). *Structured outputs.* https://docs.ollama.com/capabilities/structured-outputs

Cited as the authoritative vendor source for the `format` parameter, and identified as vendor
documentation rather than peer-reviewed work at the point of use in §A.1.

## Consulted and deliberately not cited

Three vendor and blog sources informed the literature search and are **not cited as evidence**
anywhere in this report. They are listed so that the claims discussed in §7.6 can be traced and
tested by a reader who wants to check them. One of them reports a model returning empty JSON
under constrained decoding, which would have corroborated this project's own defect; it is not
cited because this project's five-model run does not reproduce it.

- InsiderLLM. *Best local LLMs for structured output.*
  https://insiderllm.com/guides/structured-output-local-llms/
- AIMultiple. *Invoice OCR benchmark: LLMs vs OCRs.* https://aimultiple.com/invoice-ocr
- Meta-Intelligence. *SLM enterprise edge AI 2026.*
  https://www.meta-intelligence.tech/en/insight-slm-enterprise

## Still missing

The industry-context figure in §1.1, that manual invoice handling costs three to five minutes per
document, **has no source.** It currently comes from this project's own briefing document and must
be replaced with a citable figure or removed. Gómez and Sánchez (2026) and Khanchandani et al.
(2025) both motivate their work with processing-cost claims and are the most likely places to
find one.

Figures and tables generated from this project's own code still require captions and source
statements.
