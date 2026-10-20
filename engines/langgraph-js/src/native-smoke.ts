import { Annotation, END, START, StateGraph } from "@langchain/langgraph";

const SmokeState = Annotation.Root({
  value: Annotation<number>,
});

export function buildNativeSmokeGraph() {
  return new StateGraph(SmokeState)
    .addNode("increment", (state) => ({ value: state.value + 1 }))
    .addEdge(START, "increment")
    .addEdge("increment", END)
    .compile();
}
