Feature: Call Task
  As an implementer of the workflow DSL
  I want catalog calls to use the common function boundary
  So that engine adapters preserve portable call semantics

  # Selected from the pinned upstream CTK call feature. The MockTransport
  # supplies the deterministic response normally returned by Petstore.
  Scenario: Call HTTP With Content Output
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: http-call-with-content-output
      version: '1.0.0'
    do:
      - findPet:
          call: http
          with:
            method: get
            endpoint:
              uri: https://petstore.swagger.io/v2/pet/findByStatus?status={status}
          output:
            as: .[0]
    """
    And given the workflow input is:
    """yaml
    status: available
    """
    When the workflow is executed
    Then the workflow should complete
    And the workflow output should have properties 'id', 'name', 'status'

  # Selected from the pinned upstream CTK call feature. The deterministic
  # transport returns a list and the jq length filter projects its size.
  Scenario: Call OpenAPI With Content Output
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: openapi-call-with-content-output
      version: '1.0.0'
    do:
      - findPet:
          call: openapi
          with:
            document:
              endpoint: "https://petstore.swagger.io/v2/swagger.json"
            operationId: findPetsByStatus
            parameters:
              status: ${ .status }
          output:
            as: . | length
    """
    And given the workflow input is:
    """yaml
    status: available
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    1
    """

  # Selected from the pinned upstream CTK call feature. Response metadata is
  # projected through the common HTTP client without exposing auth headers.
  Scenario: Call HTTP With Response Output
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: http-call-with-response-output
      version: '1.0.0'
    do:
      - getPet:
          call: http
          with:
            method: get
            endpoint:
              uri: https://petstore.swagger.io/v2/pet/{petId}
            output: response
    """
    And given the workflow input is:
    """yaml
    petId: 1
    """
    When the workflow is executed
    Then the workflow should complete
    And the workflow output should have properties 'request', 'request.method', 'request.uri', 'request.headers', 'headers', 'statusCode', 'content'
    And the workflow output should have properties 'content.id', 'content.name', 'content.status'

  # Selected from the pinned upstream CTK call feature. OpenAPI response
  # calls share the same response projection as HTTP calls.
  Scenario: Call OpenAPI With Response Output
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: openapi-call-with-response-output
      version: '1.0.0'
    do:
      - getPet:
          call: openapi
          with:
            document:
              endpoint: "https://petstore.swagger.io/v2/swagger.json"
            operationId: getPetById
            parameters:
              petId: ${ .petId }
            output: response
    """
    And given the workflow input is:
    """yaml
    petId: 1
    """
    When the workflow is executed
    Then the workflow should complete
    And the workflow output should have properties 'request', 'request.method', 'request.uri', 'request.headers', 'headers', 'statusCode', 'content'
    And the workflow output should have properties 'content.id', 'content.name', 'content.status'

  Scenario: Built In Language Model Call
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: call
      version: '1.0.0'
    do:
      - ask:
          call: llm:1.0.0@default
          with:
            input: hello from the CTK
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    response: hello from the CTK
    """
