# Product Intelligence & Experimentation Platform

A product-analytics and A/B-testing platform running on simulated e-commerce data with known, planted effects — built to validate the engine against ground truth, not just "the code runs."

Status: under construction, built layer by layer. See the architecture and build-plan docs for the full design.

## Stack
Python (pandas, scipy, statsmodels, scikit-learn) for analysis, running on a schedule in GitHub Actions. Next.js + TypeScript frontend reading precomputed results. Neon Postgres for storage. No paid services.

## Layout
```
simulator/      event generator + planted-effects config
pipeline/       load to Postgres, daily job entry point
analytics/      DAU/MAU, funnels, retention, revenue
experiments/    power, assignment, SRM, significance tests, CUPED
intelligence/   churn model, anomaly detection, drop-off ranking
insights/       LLM prompts + summary writer
notebooks/      one notebook per module
tests/          pytest
web/            Next.js app
```
