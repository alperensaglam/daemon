#!/usr/bin/env node
'use strict';

/**
 * build/icon.png uretir.
 *
 * Depoda ikili varlik tutmuyoruz (bkz. src/main/icon.js); pencere ve tepsi
 * ikonu calisma aninda uretiliyor. Ama electron-builder diskte gercek bir
 * dosya ister. 1024x1024 tek bir PNG yeterlidir: builder bundan macOS icin
 * .icns, Windows icin .ico turetir.
 */

const fs = require('fs');
const path = require('path');
const { buildIcon } = require('../src/main/icon');

const outDir = path.join(__dirname, '..', 'build');
const outFile = path.join(outDir, 'icon.png');

fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(outFile, buildIcon(1024));

console.log(`Ikon yazildi: ${path.relative(process.cwd(), outFile)}`);
