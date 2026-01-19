#!/bin/bash
# Setup script for Ag3ntum Web UI
# Installs npm dependencies for the React/Vite frontend

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_CLIENT_DIR="$SCRIPT_DIR/src/web_terminal_client"

echo "🔧 Setting up Ag3ntum Web UI..."
echo ""

# Check if npm is installed
if ! command -v npm &> /dev/null; then
    echo "❌ npm is not installed. Please install Node.js and npm first."
    exit 1
fi

echo "📦 Installing npm dependencies in $WEB_CLIENT_DIR..."
cd "$WEB_CLIENT_DIR"
npm install

echo ""
echo "✅ Web UI setup complete!"
echo ""
echo "To start the development server:"
echo "  cd $WEB_CLIENT_DIR"
echo "  npm run dev"
echo ""
echo "Or use VSCode launch configuration: 'Full Stack (Backend + Web UI)'"

