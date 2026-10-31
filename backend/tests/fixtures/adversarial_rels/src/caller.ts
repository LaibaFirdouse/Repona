import { create as makeAlpha, uniqueAlpha } from "@dup/alpha";
import { missingThing } from "./does-not-exist";
import * as host from "./alpha";

export function runPipeline() {
  const a = makeAlpha();
  const b = uniqueAlpha();
  const c = host.create();
  create();
  execute();
  console.log(a, b, c);
  return missingThing;
}
