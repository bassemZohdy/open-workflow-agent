Feature: For Task Output
  As an implementer of the workflow DSL
  I want array iteration output to remain portable
  So that each iteration can be collected without leaking engine state

  Scenario: For Task Collects Per-Item Results
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: for-output
      version: '1.0.0'
    do:
      - decorate:
          for:
            each: color
            in: .colors
          do:
            - mark:
                set:
                  processed: '${ .processed + [{ color: $color, position: $index }] }'
    """
    And given the workflow input is:
    """yaml
    colors: [ red, green, blue ]
    processed: []
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    processed:
      - color: red
        position: 0
      - color: green
        position: 1
      - color: blue
        position: 2
    """
