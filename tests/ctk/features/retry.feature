Feature: Retry Policy
  As an implementer of the workflow DSL
  I want retry policy to recover transient catalog failures
  So that engine adapters preserve portable retry semantics

  Scenario: Retry A Transient Model Failure
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: retry
      version: '1.0.0'
    do:
      - response:
          try:
            - model:
                call: llm:1.0.0@default
                with:
                  prompt: retryable
          catch:
            retry:
              limit:
                attempt:
                  count: 1
    """
    And the fake model should fail 1 time before succeeding
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    response: retryable
    """
