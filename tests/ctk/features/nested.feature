Feature: Nested Task Input
  As an implementer of the workflow DSL
  I want nested task input expressions to remain portable
  So that engine adapters preserve scoped data flow

  Scenario: Nested Task Receives Selected Input
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: nested-input
      version: '1.0.0'
    do:
      - outer:
          do:
            - inner:
                input:
                  from: ${ .question }
                set:
                  nested: ${ . }
    """
    And given the workflow input is:
    """yaml
    question: hello from CTK
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    nested: hello from CTK
    """
