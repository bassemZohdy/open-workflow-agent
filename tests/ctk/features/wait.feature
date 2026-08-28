Feature: Wait Task
  As an implementer of the workflow DSL
  I want to ensure that wait tasks pause and then resume execution
  So that my implementation conforms to the expected behavior

  Scenario: Wait Task Resumes After The Requested Duration
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: wait
      version: '1.0.0'
    do:
      - pause:
          wait:
            milliseconds: 10
      - finish:
          set:
            completed: true
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    completed: true
    """
