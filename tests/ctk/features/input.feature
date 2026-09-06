Feature: Input Validation
  As an implementer of the workflow DSL
  I want workflow input schemas to be enforced portably
  So that engine adapters preserve the common validation boundary

  Scenario: Valid Input Reaches A Typed Task
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: input-validation
      version: '1.0.0'
    input:
      schema:
        document:
          type: object
          required: [question]
          properties:
            question:
              type: string
    do:
      - answer:
          set:
            accepted: ${ .question }
    """
    And given the workflow input is:
    """yaml
    question: hello from CTK
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    accepted: hello from CTK
    """

  Scenario: Invalid Input Is Rejected Before Task Execution
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: input-invalid
      version: '1.0.0'
    input:
      schema:
        document:
          type: object
          required: [question]
          properties:
            question:
              type: string
    do:
      - answer:
          set:
            accepted: ${ .question }
    """
    And given the workflow input is:
    """yaml
    wrong: input
    """
    When the workflow is executed
    Then the workflow should fault
