Feature: Policy Tasks
  As an implementer of the workflow DSL
  I want retry, catch, and timeout policy to remain portable
  So that engine adapters preserve common fault-recovery semantics

  Scenario: Catch Recovers A Controlled Failure Before A Timeout
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: policy
      version: '1.0.0'
    do:
      - recover:
          try:
            - fail:
                raise:
                  error: controlled failure
          catch:
            errors:
              with:
                detail: controlled failure
            do:
              - markRecovered:
                  set:
                    recovered: true
      - pause:
          wait:
            milliseconds: 0
          timeout:
            after:
              seconds: 1
      - finish:
          set:
            completed: true
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    completed: true
    """
