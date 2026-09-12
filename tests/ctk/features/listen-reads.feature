Feature: Event Read Modes
  As an implementer of the workflow DSL
  I want listen read modes to preserve the common event envelope
  So that workflows can choose data or envelope projections portably

  Scenario: Listen Task Reads The Event Envelope
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: listen-envelope
      version: '1.0.0'
    do:
      - waitForEvent:
          listen:
            to:
              one:
                with:
                  type: ctk.example.envelope
                  subject: ctk-envelope-1
            read: envelope
      - finish:
          set:
            eventType: ${ .type }
            eventSource: ${ .source }
            eventValue: ${ .data.value }
    """
    And an event should be delivered after the workflow waits:
    """yaml
    id: ctk-envelope-event-1
    source: urn:owa:ctk
    type: ctk.example.envelope
    subject: ctk-envelope-1
    data:
      value: 7
    """
    When the workflow is executed
    Then the workflow should complete with output:
    """yaml
    eventType: ctk.example.envelope
    eventSource: urn:owa:ctk
    eventValue: 7
    """
