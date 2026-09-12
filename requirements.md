Overview: Improve Billing through the Conductor execution loop.

Delivery Context:
- Current stage: development
- Validated stages: none
- Rollout strategy: canary

Requirements Register:
- REQ-001: Inspect and record current repository, runtime, or job evidence before selecting an operation.
- REQ-002: Implement only the scoped change, job update, or progress-monitoring action supported by that evidence.
- REQ-003: Preserve secure, resilient behaviour and avoid destructive commands.
- REQ-004: Update or add tests covering the changed path, or provide the relevant live operational check.
- REQ-005: Run verification commands and report the outcome.
- REQ-006: Leave unrelated files untouched.
- REQ-007: Record rollback/recovery steps and the acceptance signal proving the gap is closed.
- REQ-008: Preserve staged progression and rollout governance metadata.
- REQ-009: Capture a fresh protected-target readiness baseline before any change.
- REQ-010: Use the selected canary or red-green rollout strategy and verify the post-rollout health window.
- REQ-011: Automatically revert the exact produced commit without rewriting history if health or verification degrades.
- REQ-012: Verify rollback readiness and recovery before finalising the delivery.
- REQ-013: When runtime rollout or restart work is needed, use the available Ansible automation context: {"ansible_root":"/srv/swarmhpc/ansible","config_path":"/srv/swarmhpc/ansible/ansible.cfg","host_targets":["rk1"],"hosts":["spirit"],"inventory_path":"/srv/swarmhpc/ansible/inventory/hosts.ini","playbooks":["continuum_tenant_billing_site.yml","continuum_tenant_nmchain_site.yml"],"repo_root":"/srv/swarmhpc","roles_path":"/srv/swarmhpc/ansible/roles","secrets_root":"/srv/swarmhpc/ansible/.secrets"}.

Work Item Summary:
Billing depends on shared data services but no clear persistent-storage profile was inferred from Ansible. Confirm PVCs or durable mounts before further automation.

Authoritative delivery constraints (mandatory; implement and verify these, do not merely describe them):
- No structured delivery constraints were supplied; follow the work-item summary exactly.

Plan JSON:
{"action":"verify_persistent_storage","finding_id":"12946738-900d-4d08-a128-1e1687deccff","finding_key":"storage_profile:billing","service":"billing"}

Planner guidance (advisory; it must not weaken or contradict the authoritative work-item requirements):
This work item verifies the persistent storage strategy for the Billing service to ensure data durability before further automation. The operation focuses on inspecting the repository and Ansible configuration to confirm the absence or presence of defined Persistent Volume Claims (PVCs) or durable mounts.

Requirements Register:
- REQ-001: Confirm absence or presence of a defined PVC or durable mount profile for the Billing service in the current configuration.
- REQ-002: Validate that any existing storage configuration supports the data durability requirements of the Billing service.
- REQ-003: Ensure the verification process is non-destructive and does not modify production files or runtime state.
- REQ-004: If a storage profile is missing, recommend a minimal viable configuration for the next development iteration.
- REQ-005: Execute a canary rollout strategy to monitor storage health metrics for a 15-minute window before full deployment.
- REQ-006: Define automatic rollback triggers based on storage degradation signals or test failure rates exceeding the baseline.
- REQ-007: Verify that the Ansible configuration at /srv/swarmhpc/ansible is consistent with the repository state.
- REQ-008: Ensure the target host 'spirit' is accessible for read-only inspection of volume attachment status.
- REQ-009: Document all findings in a technical report including evidence, uncertainty levels, and recommended actions.
- REQ-010: Avoid any changes to production logic or existing files during this verification phase.
- REQ-011: If a storage profile is confirmed, run a targeted unit test suite to validate the current baseline stability.
- REQ-012: If no profile is found, generate a technical recommendation to define a minimal PVC or mount strategy in the next iteration.


Protected rollout contract (mandatory): capture a fresh readiness baseline before any change; use the selected canary or red_green strategy; verify health throughout the post-rollout window; if health or verification degrades, automatically revert the exact produced commit without rewriting history, rerun tests and GitHub Actions, and verify recovery.