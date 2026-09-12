Feature: Task Data Schemas
  As an implementer of the workflow DSL
  I want task output schemas to be enforced portably
  So that invalid task data becomes a common workflow fault

  Scenario: Task Output Satisfies Its Schema
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: task-schema-valid
      version: '1.0.0'
    do:
      - answer:
          set:
            answer: 42
          output:
            schema:
              document:
                type: object
                required: [answer]
                properties:
                  answer:
                    type: integer
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    answer: 42
    """

  Scenario: Task Output Violating Its Schema Faults
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: task-schema-invalid
      version: '1.0.0'
    do:
      - answer:
          set:
            answer: wrong-type
          output:
            schema:
              document:
                type: object
                required: [answer]
                properties:
                  answer:
                    type: integer
    """
    When the workflow is executed
    Then the workflow should fault
