'use strict';

const files = require('./tools/files');
const shell = require('./tools/shell');
const system = require('./tools/system');
const web = require('./tools/web');

const ALL = [...files, ...shell, ...system, ...web];

const byName = new Map(ALL.map((t) => [t.name, t]));

/** OpenAI function-calling formatinda arac semalari. */
function toolSchemas() {
  return ALL.map((t) => ({
    type: 'function',
    function: {
      name: t.name,
      description: t.description,
      parameters: t.parameters || { type: 'object', properties: {} },
    },
  }));
}

function get(name) {
  return byName.get(name);
}

function list() {
  return ALL.map((t) => ({ name: t.name, risk: t.risk, description: t.description }));
}

module.exports = { toolSchemas, get, list, ALL };
