Feature: Event Tasks
  As an implementer of the workflow DSL
  I want listen tasks to resume from a matching event
  So that eventing remains portable across engines

  Scenario: Listen Task Receives One Event
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: default
      name: listen
      version: '1.0.0'
    do:
      - waitForEvent:
          listen:
            to:
              one:
                with:
                  type: ctk.example.received
                  subject: ctk-listen-1
            read: data
      - finish:
          set:
            received: ${ .value }
    """
    And an event should be delivered after the workflow waits:
    """yaml
    id: ctk-listen-event-1
    source: urn:owa:ctk
    type: ctk.example.received
    subject: ctk-listen-1
    data:
      value: 42
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    received: 42
    """
