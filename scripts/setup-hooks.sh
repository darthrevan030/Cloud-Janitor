#!/bin/sh
cp scripts/hooks/post-commit .git/hooks/post-commit
chmod +x .git/hooks/post-commit
echo "Git hooks installed successfully."
