import pug from 'pug';
import chokidar from 'chokidar';
import { $ } from "bun";

const filters = {
	"repo": function (text: string) {
		const display = `${text.replace("Delnegend/", "")}`
		return `<td><a href="https://github.com/${text}">${display}</a></td>`
	},
	"tech": function (text: string) {
		text = text.replaceAll("Rust", "<img src='ferris.svg' height='12px' align='center'> Rust")
		text = text.replaceAll("Go", "<img src='gopher.svg' height='12px' align='center'> Go")
		return `<td>${text}</td>`
	}
}

async function build() {
	let content = pug.renderFile('README.pug', {filters})
	content = content.replaceAll(/\[(.*?)\]\((.*?)\)/g, "<a href='https://$2'>$1</a>")
	content = `<samp>\n\n${content}\n\n</samp>`
	await Bun.write("README.md", content)
}

await build()

if (process.argv.includes("-w")) {
	chokidar.watch('README.pug').on('change', async () => {
		await build()
	})
}

// p for publish
if (process.argv.includes("-p")) {
	await $`git add . && git commit -m ":rocket:" && git push`
}