Feature: Event Tasks
  As an implementer of the workflow DSL
  I want emit tasks to publish CloudEvents through the common event service
  So that eventing remains portable across engines

  Scenario: Emit Task Publishes An Event
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: emit
      version: '1.0.0'
    do:
      - publish:
          emit:
            event:
              with:
                id: ctk-emit-1
                source: urn:owa:ctk
                type: ctk.example.emitted
                data:
                  value: 42
      - finish:
          set:
            completed: true
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    completed: true
    """
    And the event bus should contain type 'ctk.example.emitted'
