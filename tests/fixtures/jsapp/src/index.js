import { greet } from "./lib/greet";
const util = require("./lib/util.js");
import React from "react";

export function main() {
  if (greet() && util) return 1;
}
