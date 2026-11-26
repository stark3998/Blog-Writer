## Launch checklist

- [x] Threat model reviewed
- [x] Conditional access policies exported
- [ ] Break-glass accounts tested
  - [ ] Primary account
  - [x] Secondary account
- [ ] Workload identities rotated with `az ad app credential reset`
- [ ] Read the [runbook](/blog/runbook) and sign off **in writing**

Task items can hold other blocks:

- [ ] Parent task with a plain list underneath
  - first note
  - second note
- [ ] Task with a code block
  ```bash
  terraform plan -out tfplan
  ```
