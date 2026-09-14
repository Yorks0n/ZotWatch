# P2A red baseline

Baseline under test: `v2-e5-baseline` (`c439bb9046b8c863b12348de8e90e735017ba7e3`).

The first P2A contract commit deliberately introduces tests before production
workflow code.  The expected initial failures are missing
`zotwatch.workflow.{identity,checkpoint,artifacts,results}` modules and missing
`.github/workflows/{run,publish-pages}.yml`.  Existing E0–E5 tests remain the
control group and must stay green.  No existing golden or fixture is changed.
