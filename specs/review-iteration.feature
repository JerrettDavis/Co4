Feature: PR review iteration end to end
  A reviewer's feedback on a Co4 draft PR becomes a governed revision round: routed back to
  a contributor device, executed on top of the approved commit, approved by a person, and
  fast-forwarded onto the same PR -- until the PR is merged or a maintainer must step in.

  Scenario: Changes requested, revised, re-reviewed and merged
    Given a contributor's device published a human-approved draft PR
    When a maintainer requests changes on the PR
    Then a revision round is queued for the PR
    When the original contributor's device polls for work
    Then it receives a revision lease carrying the reviewer feedback
    When the device completes the revision on top of the approved commit
    And the contributor approves the exact revision commit
    Then the App fast-forwards the PR branch to the approved revision commit
    And the PR receives a round status comment for the maintainer
    And GitHub's synchronize event for the new head is accepted
    When the maintainer approves the PR on GitHub
    Then the submission is approved
    When the PR is merged
    Then the work item and the submission are merged

  Scenario: The revision round budget is exhausted and a maintainer is asked to step in
    Given a project that allows only one revision round
    And a contributor's device published a human-approved draft PR
    When a maintainer requests changes on the PR
    And the original contributor's device polls for work
    And the device completes the revision on top of the approved commit
    And the contributor approves the exact revision commit
    And a maintainer requests changes on the revised PR
    Then the submission is escalated for a maintainer
    And the issue and the PR are labelled co4:needs-human
    And the PR receives an escalation comment for the maintainer
    And no further revision lease is offered
