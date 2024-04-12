param (
    [string]$outputPath
)

# Clone repository
git clone --depth 1 https://github.com/Eternet/github.git tmp_repo

# Copy .editorconfig from cloned repository
cp tmp_repo/files/.editorconfig $outputPath

# Configure git and push if are changes to the file
git config --global user.name "GithubActionBot"
git config --global user.email "<>"
git add .editorconfig
git commit -m "Update .editorconfig"
git push
