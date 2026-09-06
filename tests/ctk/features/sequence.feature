Feature: Sequential Data Flow
  As an implementer of the workflow DSL
  I want sequential tasks to pass data through the workflow
  So that engine adapters preserve declaration-order state changes

  Scenario: A Later Task Uses An Earlier Task Result
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: sequence
      version: '1.0.0'
    do:
      - copy:
          set:
            copied: ${ .question }
      - finish:
          set:
            answer: ${ .copied }
    """
    And given the workflow input is:
    """yaml
    question: hello from CTK
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    answer: hello from CTK
    """
