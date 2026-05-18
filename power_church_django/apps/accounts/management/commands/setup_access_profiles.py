from __future__ import annotations

from django.core.management.base import BaseCommand, CommandParser

from power_church_django.services.access_control import create_or_update_admin, ensure_access_control


class Command(BaseCommand):
    help = "Instala permissoes e grupos padrao do Power Church no banco Django."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--admin-user", default="", help="Cria ou atualiza um superusuario local.")
        parser.add_argument("--admin-email", default="", help="E-mail do superusuario local.")
        parser.add_argument("--admin-password", default="", help="Senha inicial do superusuario local.")

    def handle(self, *args: object, **options: object) -> None:
        summary = ensure_access_control()
        self.stdout.write(self.style.SUCCESS("Permissoes e grupos padrao instalados."))
        self.stdout.write(
            "Permissoes: {total_permissions} ({created_permissions} novas, {updated_permissions} atualizadas)".format(
                **summary
            )
        )
        self.stdout.write("Grupos: {total_groups} ({created_groups} novos)".format(**summary))
        admin_user = str(options.get("admin_user") or "").strip()
        if admin_user:
            user = create_or_update_admin(
                username=admin_user,
                email=str(options.get("admin_email") or "").strip(),
                password=str(options.get("admin_password") or ""),
            )
            if options.get("admin_password"):
                detail = "senha definida pelo comando"
            else:
                detail = "senha nao definida; defina pelo admin ou via createsuperuser"
            self.stdout.write(self.style.WARNING(f"Superusuario {user.username} pronto ({detail})."))

