Feature: Try Catch Tasks
  As an implementer of the workflow DSL
  I want a caught failure to enter its recovery branch
  So that engine adapters preserve portable error handling

  Scenario: Try Catch Recovers A Controlled Failure
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: try-catch
      version: '1.0.0'
    do:
      - guarded:
          try:
            - fail:
                raise:
                  error: controlled failure
          catch:
            do:
              - recover:
                  set:
                    recovered: true
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    recovered: true
    """
