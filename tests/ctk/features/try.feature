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

  # Selected from the pinned upstream CTK try feature. The catch filter does
  # not match the deterministic 404, so the original error remains uncaught.
  Scenario: Try Raise Uncaught Error
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: try-catch-503
      version: '1.0.0'
    do:
      - tryGetPet:
          try:
            - getPet:
                call: http
                with:
                  method: get
                  endpoint:
                    uri: https://petstore.swagger.io/v2/pet/getPetByName/{petName}
          catch:
            errors:
              with:
                type: https://open-workflow-specification.org/dsl/errors/types/communication
                status: 503
            as: err
            do:
              - setError:
                  set:
                    error: ${ $err }
    """
    And given the workflow input is:
    """yaml
    petName: Milou
    """
    When the workflow is executed
    Then the workflow should fault

  # Selected from the pinned upstream CTK try feature. The caught error is
  # exposed to the handler using the Open Workflow error vocabulary.
  Scenario: Try Handle Caught Error
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: try-catch-404
      version: '1.0.0'
    do:
      - tryGetPet:
          try:
            - getPet:
                call: http
                with:
                  method: get
                  endpoint:
                    uri: https://petstore.swagger.io/v2/pet/getPetByName/{petName}
          catch:
            errors:
              with:
                type: https://open-workflow-specification.org/dsl/errors/types/communication
                status: 404
            as: err
            do:
              - setError:
                  set:
                    error: ${ $err }
    """
    And given the workflow input is:
    """yaml
    petName: Milou
    """
    When the workflow is executed
    Then the workflow should complete
    And the workflow output should have properties 'error', 'error.type', 'error.status', 'error.title'
    And the workflow output should have a 'error.instance' property with value:
    """yaml
    /do/0/tryGetPet/try/0/getPet
    """
