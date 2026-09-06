Feature: Data Flow
  As an implementer of the workflow DSL
  I want to ensure that data flows correctly through the workflow
  So that my implementation conforms to the expected behavior

  # Selected from the pinned upstream CTK data-flow feature. The input
  # filtering case is deterministic and does not require an external service.
  Scenario: Input Filtering
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: output-filtering
      version: '1.0.0'
    do:
      - setPlayerId:
          input:
            from: .user.claims.subject
          set:
            playerId: ${ . }
    """
    And given the workflow input is:
    """yaml
    user:
      claims:
        subject: 6AsnRgGEB0q2O7ux9JXFAw
    """
    When the workflow is executed
  Then the workflow should complete with output:
    """yaml
    playerId: 6AsnRgGEB0q2O7ux9JXFAw
    """

  # Selected from the pinned upstream CTK data-flow feature. The MockTransport
  # supplies the response normally returned by the public Petstore endpoint.
  Scenario: Output Filtering
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: output-filtering
      version: '1.0.0'
    do:
      - getPet:
          call: http
          with:
            method: get
            endpoint:
              uri: https://petstore.swagger.io/v2/pet/{petId}
          output:
            as: .id
    """
    And given the workflow input is:
    """yaml
    petId: 1
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    1
    """

  # Selected from the pinned upstream CTK data-flow feature. This verifies
  # non-object task output and access to the original workflow input.
  Scenario: Use Non-object Output
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: non-object-output
      version: '1.0.0'
    do:
      - getPetById1:
          call: http
          with:
            method: get
            endpoint:
              uri: https://petstore.swagger.io/v2/pet/{petId}
          output:
            as: .id
      - getPetById2:
          call: http
          with:
            method: get
            endpoint:
              uri: https://petstore.swagger.io/v2/pet/2
          output:
            as: '{ ids: [ $input, .id ] }'
    """
    And given the workflow input is:
    """yaml
    petId: 1
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    ids: [ 1, 2 ]
    """
