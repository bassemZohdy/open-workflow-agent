Feature: Data Transform Tasks
  As an implementer of the workflow DSL
  I want input, output, and export transforms to remain portable
  So that engine adapters preserve common data-flow semantics

  Scenario: Transform Shapes A Value And Exports The Last Result
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: transform
      version: '1.0.0'
    input:
      from: ${ .payload }
    do:
      - transform:
          input:
            from: ${ .value }
            schema:
              document:
                type: integer
          set:
            doubled: ${ . }
          output:
            as:
              doubled: ${ .doubled }
          export:
            as:
              last_value: ${ .doubled }
    output:
      as: ${ .doubled }
    """
    And given the workflow input is:
    """yaml
    payload:
      value: 21
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    21
    """
