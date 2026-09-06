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
