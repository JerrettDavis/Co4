Feature: GitHub device flow sign-in
  A person without a reachable OAuth callback (for example, a loopback-only
  install) signs in by entering a short code on GitHub. Co4 must turn a GitHub
  approval into a server-side session, and must never hand the raw session
  token to page scripts.

  Background:
    Given Co4 is running in GitHub-connected mode
    And the visitor has started device-flow sign-in

  Scenario: Approving the code on GitHub signs the visitor in
    When the visitor approves the code on GitHub as "octo-dev"
    And the page polls for the result
    Then the poll reports the visitor as authorized as "octo-dev"
    And the visitor is signed in as "octo-dev"
    And the device code cannot be redeemed a second time

  Scenario Outline: A code that is not approved does not sign anyone in
    When GitHub reports the code as <github_error>
    And the page polls for the result
    Then the poll reports the sign-in as <outcome>
    And no session cookie is issued
    And the visitor is still signed out

    Examples:
      | github_error  | outcome |
      | expired_token | expired |
      | access_denied | denied  |

  Scenario: A code that is still waiting for approval keeps the visitor signed out
    When GitHub reports the code as authorization_pending
    And the page polls for the result
    Then the poll reports the sign-in as pending
    And no session cookie is issued
    And the visitor is still signed out

  Scenario: The session cookie is HttpOnly and never exposed to page scripts
    When the visitor approves the code on GitHub as "octo-dev"
    And the page polls for the result
    Then the session cookie is set by the server with the HttpOnly attribute
    And the session cookie is scoped to the whole site with SameSite=Lax
    And the poll response body does not contain the session token
