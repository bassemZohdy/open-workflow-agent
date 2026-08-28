Feature: Fork Task
  As an implementer of the workflow DSL
  I want to ensure that non-competing fork branches all execute
  So that my implementation conforms to the expected behavior

  Scenario: Fork Task With Concurrent Non-Competing Branches
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: fork-concurrent
      version: '1.0.0'
    do:
      - branchConcurrent:
          fork:
            compete: false
            branches:
              - setRed:
                  set:
                    colors: ${ .colors + ["red"] }
              - setGreen:
                  set:
                    colors: ${ .colors + ["green"] }
              - setBlue:
                  set:
                    colors: ${ .colors + ["blue"] }
    """
    When the workflow is executed
    Then the workflow should complete
    And the workflow output should have a 'colors' property containing 3 items
