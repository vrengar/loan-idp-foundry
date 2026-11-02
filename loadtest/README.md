# Load testing — IDP demo

Two complementary tracks:

## Track A — Simple (tag-based Cost Management)

1. Provision Azure Load Testing in `IDP-rg`:
   ```powershell
   az load create -n alt-idp -g IDP-rg -l eastus
   ```
2. Run the JMX with this folder uploaded:
   ```powershell
   az load test create --name idp-baseline --load-test-resource alt-idp -g IDP-rg `
     --test-plan ./loadtest.jmx --engine-instances 2 --description "IDP baseline"
   az load test-run create --name run1 --test-id idp-baseline --load-test-resource alt-idp -g IDP-rg
   ```
3. Wait ~24h for billing rollup, then in **Cost Management → Cost analysis** filter by tag
   `app=idp-demo`. That tag is applied to every resource in `infra/`. You see total USD spent
   for the test window — divide by the number of PDFs sent during the test for **$/document**.

## Track B — Advanced (per-tenant cost allocation via App Insights)

The API emits `di.pages.processed` custom events with `tenantId`, `model`, `pageCount`,
`estimatedCostUsd`. Open Application Insights → Logs and run
[`cost-allocation.kql`](cost-allocation.kql) to chargeback per tenant in real time
(no waiting on Cost Management rollups). Agent runs and Claude calls are traced too: run
[`agent-steps.kql`](agent-steps.kql) for agent volume, token use, and review timing.

Configure tenants in the JMX `tenantId` CSV (`tenants.csv`) so a single load run
exercises a multi-tenant mix.

## Recommended scenarios

| Scenario | Threads | Ramp | Hold | Goal |
|----------|--------:|-----:|-----:|------|
| Smoke    |   2     |  30s |  2m  | Verify deploy + telemetry path |
| Baseline |  20     |  1m  | 10m  | Steady throughput, $/doc baseline |
| Burst    |  80     |  30s |  5m  | Find DI throttling (HTTP 429) |
| Soak     |  10     |  2m  | 60m  | Stability + memory leak check |

DI service limits (S0): 15 TPS analyze, 200 TPM read. See
https://learn.microsoft.com/azure/ai-services/document-intelligence/service-limits
