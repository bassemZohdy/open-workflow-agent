Feature: Protocol Call Tasks
  As an implementer of the workflow DSL
  I want protocol calls to use the common protocol boundary
  So that engine adapters preserve portable HTTP, MCP, A2A, and OpenAPI semantics

  Scenario: HTTP Call Uses The Mock Protocol Boundary
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: http-call
      version: '1.0.0'
    do:
      - getHttp:
          call: http
          with:
            method: GET
            endpoint: https://service.test/http
          timeout:
            after:
              seconds: 5
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    path: /http
    """

  Scenario: MCP Call Uses The Mock Protocol Boundary
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: mcp-call
      version: '1.0.0'
    do:
      - callTool:
          call: mcp
          with:
            protocolVersion: '2026-07-28'
            method: tools/call
            parameters:
              name: lookup
              arguments:
                query: ctk
            transport:
              http:
                endpoint: https://service.test/mcp
            timeout:
              seconds: 5
          timeout:
            after:
              seconds: 5
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    path: /mcp
    """

  Scenario: A2A Call Uses The Mock Protocol Boundary
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: a2a-call
      version: '1.0.0'
    do:
      - askAgent:
          call: a2a
          with:
            method: message/send
            server: https://service.test/a2a
            parameters:
              message:
                messageId: ctk-a2a-message
                role: ROLE_USER
                parts:
                  - kind: text
                    text: hello from CTK
          timeout:
            after:
              seconds: 5
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    path: /a2a
    """

  Scenario: OpenAPI Call Uses The Mock Protocol Boundary
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: openapi-call
      version: '1.0.0'
    do:
      - lookupOpenapi:
          call: openapi
          with:
            document:
              endpoint: https://service.test/openapi
            operationId: lookup
            parameters:
              query: ctk
          timeout:
            after:
              seconds: 5
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    path: /openapi
    """

  Scenario: HTTP Call Converts A Mock Protocol Error Into A Workflow Fault
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: http-error
      version: '1.0.0'
    do:
      - getHttpError:
          call: http
          with:
            method: GET
            endpoint: https://service.test/error
          timeout:
            after:
              seconds: 5
    """
    When the workflow is executed
    Then the workflow should fault

  Scenario: MCP Call Converts A Mock Protocol Error Into A Workflow Fault
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: mcp-error
      version: '1.0.0'
    do:
      - callToolError:
          call: mcp
          with:
            protocolVersion: '2026-07-28'
            method: tools/call
            parameters:
              name: lookup
              arguments:
                query: ctk
            transport:
              http:
                endpoint: https://service.test/error
            timeout:
              seconds: 5
          timeout:
            after:
              seconds: 5
    """
    When the workflow is executed
    Then the workflow should fault

  Scenario: A2A Call Converts A Mock Protocol Error Into A Workflow Fault
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: a2a-error
      version: '1.0.0'
    do:
      - askAgentError:
          call: a2a
          with:
            method: message/send
            server: https://service.test/error
            parameters:
              message:
                messageId: ctk-a2a-error
                role: ROLE_USER
                parts:
                  - kind: text
                    text: hello from CTK
          timeout:
            after:
              seconds: 5
    """
    When the workflow is executed
    Then the workflow should fault

  Scenario: OpenAPI Call Converts A Mock Protocol Error Into A Workflow Fault
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: openapi-error
      version: '1.0.0'
    do:
      - lookupOpenapiError:
          call: openapi
          with:
            document:
              endpoint: https://service.test/error
            operationId: lookup
            parameters:
              query: ctk
          timeout:
            after:
              seconds: 5
    """
    When the workflow is executed
    Then the workflow should fault
