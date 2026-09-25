Feature: Governed cooperative contribution
  Scenario: Only validated and permitted work can be allocated
    Given a project requires validation and verified contributors
    And an unverified contributor watches the project
    When their device polls for an unvalidated issue
    Then no lease is allocated
    When a maintainer validates the issue and verifies the contributor
    Then a matching enabled device may receive one lease

  Scenario: Automatic execution is not permission to publish
    Given an automatically allocated contribution passes baseline, red, green and verification
    When the worker submits its review package
    Then no pull request exists
    And a device token cannot authorize publication
    When the contributor approves the exact tested SHA and package digest
    Then the App may create a draft pull request from that SHA

  Scenario: A recovery window protects the original contributor
    Given an allocation has had no communication for 12 hours
    When another eligible contributor requests dibs
    Then the original contributor has 12 hours to explicitly recover
    And a heartbeat alone does not cancel the dibs
    When that window expires without recovery
    Then a new generation may be allocated
    And the original worker cannot update the old lease
    And the old checkpoint is retained

  Scenario: Tests cannot be weakened to manufacture a pass
    Given the red phase changed regression tests and they failed
    When the green phase modifies those regression tests
    Then the worker blocks completion
    And no review approval or PR can follow that attempt

  Scenario: Unknown provider usage is not free work
    Given a harness does not expose complete token usage
    When its allocation is settled
    Then the quota ledger accounts for at least its reservation
    And public cost and unavailable counters are not fabricated
