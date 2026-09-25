Feature: Contributor-selected native terminal execution
  Co4 coordinates work without forcing a provider's programmatic execution mode.

  Scenario Outline: Perform a governed workflow using a native terminal
    Given a contributor has opted into interactive execution for <harness>
    And a real terminal is attached and native CLI sign-in is managed locally
    When the contributor is allocated an eligible issue
    Then Co4 launches the native UI without print, exec, or JSON-output flags
    And terminal input and resize events reach the child
    And heartbeats and checkpoints remain active
    And the contributor confirms each phase before its validation
    And the spec, behavior, red, green, and verify gates are enforced
    And final draft PR creation still requires exact-commit human review

    Examples:
      | harness |
      | claude  |
      | codex   |
      | copilot |

  Scenario: Do not silently switch execution or billing modes
    Given interactive execution is selected
    When no terminal is attached or the native invocation fails
    Then Co4 refuses or blocks execution without retrying programmatically
    And explicitly forwarded provider credentials require separate opt-in
    And existing local account billing remains unverified

  Scenario: Do not invent accounting from a terminal
    Given a native session prints JSON-looking usage text
    When Co4 prepares the review receipt
    Then interactive capture is explicitly labeled terminal_output
    And missing token and cost counters remain unknown and incomplete
    And private prompts and account details are not public receipt fields

  Scenario: Stop and recover without losing governance
    Given an interactive allocation is active
    When the contributor presses Ctrl+] or a watchdog/lease check stops execution
    Then the child process group is stopped and terminal settings restored
    And private prompt cleanup is attempted
    And saved phase and checkpoint state remain recoverable
    And no PR is created

  Scenario: Preserve existing configurations during upgrade
    Given an alpha.1 worker configuration omits execution_mode
    When the upgraded worker starts
    Then noninteractive behavior is retained
    And an explicit --interactive option can override it locally
