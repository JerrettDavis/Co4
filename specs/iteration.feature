Feature: PR review iteration
  Scenario: A permitted reviewer's changes-requested review opens a revision round
    Given a published draft PR with an approved commit
    When a reviewer with write access requests changes on the current head
    Then the submission moves to changes requested
    And the work item is queued for revision

  Scenario: An unprivileged reviewer cannot open a revision round
    Given a published draft PR with an approved commit
    When a reviewer without write access requests changes on the current head
    Then the submission is still open

  Scenario: A permitted maintainer can request revision with a slash command
    Given a published draft PR with an approved commit
    When a maintainer comments slash co4 revise on the pull request
    Then the submission moves to changes requested

  Scenario: A review of an outdated head is ignored
    Given a published draft PR with an approved commit
    When a reviewer with write access requests changes on a stale head
    Then the submission is still open

  Scenario: A changes-requested review missing its commit id is treated as suspect
    Given a published draft PR with an approved commit
    When a reviewer with write access requests changes with no commit id
    Then the submission is still open

  Scenario: An approval missing its commit id is treated as suspect
    Given a published draft PR with an approved commit
    When a reviewer with write access approves with no commit id
    Then the submission is still open

  Scenario: The same review cannot open two revision rounds
    Given a published draft PR with an approved commit
    When a reviewer with write access requests changes on the current head
    And that same review is redelivered
    Then only one revision round was requested

  Scenario: Only one revision round is open at a time
    Given a published draft PR with an approved commit
    When a reviewer with write access requests changes on the current head
    And a second reviewer with write access requests changes on the current head
    Then only one revision round was requested

  Scenario: Reaching the round limit escalates instead of looping forever
    Given a submission that has already used every revision round its policy allows
    When a reviewer with write access requests changes on the current head
    Then the submission is escalated for a maintainer
    And a needs-human label is queued
