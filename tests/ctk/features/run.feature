Feature: Run Task

  Scenario: Run Task Invokes A Registered Child Workflow
    Given a workflow catalog should contain this child workflow:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: child
      version: '1.0.0'
    do:
      - makeChild:
          set:
            child: '${ .value }'
    """
    Given a workflow with definition:
    """yaml
    document:
      dsl: '1.0.3'
      namespace: ctk
      name: parent
      version: '1.0.0'
    do:
      - child:
          run:
            workflow:
              namespace: ctk
              name: child
              version: '1.0.0'
              input:
                value: '${ .value }'
      - finish:
          set:
            result: '${ .child }'
    """
    And given workflow input:
    """yaml
    value: from-parent
    """
    When the workflow is invoked
    Then complete with output:
    """yaml
    result: from-parent
    """
