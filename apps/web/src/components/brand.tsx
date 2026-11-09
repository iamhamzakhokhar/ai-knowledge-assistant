import Link from "next/link";
import { Icon } from "./icons";

export function Brand() {
  return (
    <Link className="brand" href="/" aria-label="KnowledgeDesk home">
      <span className="brand-mark">
        <Icon name="book" />
      </span>
      <span>
        Knowledge<span className="brand-light">Desk</span>
      </span>
    </Link>
  );
}
