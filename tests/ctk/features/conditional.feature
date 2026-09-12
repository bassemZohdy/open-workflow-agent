Feature: Conditional Tasks
  As an implementer of the workflow DSL
  I want conditional tasks to preserve portable branching semantics
  So that disabled tasks do not mutate workflow data

  Scenario: Conditional Task Runs When Its Predicate Matches
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: conditional-match
      version: '1.0.0'
    do:
      - markEnabled:
          if: .enabled
          set: '${ . + { branch: "enabled" } }'
      - finish:
          set: '${ . + { completed: true } }'
    """
    And given the workflow input is:
    """yaml
    enabled: true
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    enabled: true
    branch: enabled
    completed: true
    """

  Scenario: Conditional Task Leaves Data Unchanged When Its Predicate Fails
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: conditional-skip
      version: '1.0.0'
    do:
      - markEnabled:
          if: .enabled
          set: '${ . + { branch: "must-not-run" } }'
      - finish:
          set: '${ . + { completed: true } }'
    """
    And given the workflow input is:
    """yaml
    enabled: false
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    enabled: false
    completed: true
    """
