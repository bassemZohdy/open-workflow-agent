Feature: Flow Control
  As an implementer of the workflow DSL
  I want task transitions to preserve declaration-order flow
  So that then directives can skip tasks and end a workflow scope

  Scenario: Then Jumps Over A Task And Ends The Workflow
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: flow
      version: '1.0.0'
    do:
      - startFlow:
          set: '${ . + { started: true } }'
          then: finishFlow
      - skippedFlow:
          set: '${ . + { skipped: true } }'
      - finishFlow:
          set: '${ . + { finished: true } }'
          then: end
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    started: true
    finished: true
    """
    And startFlow should run first
    And finishFlow should run last

  # Selected from the pinned upstream CTK flow feature.
  Scenario: Implicit Sequence Flow
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: implicit-sequence
      version: '1.0.0'
    do:
      - setRed:
          set:
            colors: '${ .colors + [ "red" ] }'
      - setGreen:
          set:
            colors: '${ .colors + [ "green" ] }'
      - setBlue:
          set:
            colors: '${ .colors + [ "blue" ] }'
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    colors: [ red, green, blue ]
    """
    And setRed should run first
    And setGreen should run after setRed
    And setBlue should run after setGreen

  # Selected from the pinned upstream CTK flow feature.
  Scenario: Explicit Sequence Flow
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: explicit-sequence
      version: '1.0.0'
    do:
      - setRed:
          set:
            colors: '${ .colors + [ "red" ] }'
          then: setGreen
      - setBlue:
          set:
            colors: '${ .colors + [ "blue" ] }'
          then: end
      - setGreen:
          set:
            colors: '${ .colors + [ "green" ] }'
          then: setBlue
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    colors: [ red, green, blue ]
    """
    And setRed should run first
    And setGreen should run after setRed
    And setBlue should run after setGreen
